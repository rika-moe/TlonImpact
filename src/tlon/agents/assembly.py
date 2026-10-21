"""Member service assembly (split from agent_registry).

``assemble_member_services`` builds the services one member receives —
LLM client, memory, task manager, Docker runtime, and the tool list —
and returns it as ``MemberServices`` for ``MemberRuntime``. It is
a pure function of its parameters: the registry injects itself (it
satisfies the ``AgentLifecycle`` port structurally) plus the config
provider and paths, keeping the import direction agents → tools (the
ports live in ``tlon.lifecycle.ports`` / ``tlon.evolution.ports``).
"""

from functools import partial
from typing import TYPE_CHECKING

from tlon.agents.capabilities.catalog import list_trusted_capabilities
from tlon.agents.capabilities.staging import write_staging_capability
from tlon.agents.health import orphan_mail_provider
from tlon.agents.mail_stats import mail_stats_provider
from tlon.agents.member_runtime import MemberServices
from tlon.agents.policy import policy_solvency_provider, policy_trust_provider
from tlon.agents.prototypes.catalog import (
    get_prototype_detail,
    list_prototypes,
)
from tlon.agents.prototypes.lineage import get_prototype_lineage
from tlon.agents.prototypes.near_kin import find_near_kin
from tlon.agents.prototypes.staging import (
    append_staging_review,
    list_staging_prototypes,
    write_staging_prototype,
)
from tlon.agents.reputation import reputation_provider
from tlon.agents.session_journal import SessionJournal
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.config import DEFAULT_CONTEXT_WINDOW, AgentConfig, LLMConfig
from tlon.base.execution import MemberWriteGate
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.metrics import MetricsService
from tlon.base.paths import TlonPaths
from tlon.base.tracing.trace_store import TraceStore
from tlon.evolution.get_lineage import PrototypeLineage
from tlon.evolution.list_prototypes import PrototypeCatalog
from tlon.evolution.list_staging import StagingCatalog
from tlon.evolution.propose_behavior import BehaviorProposer
from tlon.evolution.propose_capability import CapabilityProposer
from tlon.evolution.review_prototype import PrototypeReviewer
from tlon.evolution.write_prototype import PrototypeWriter
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.operations.evolution import make_evolution_operations
from tlon.operations.filesystem import make_filesystem_operations
from tlon.operations.identity import make_identity_operations
from tlon.operations.memory import make_memory_operations
from tlon.operations.observability import make_observability_operations
from tlon.operations.shell import make_shell_operations
from tlon.operations.tasks import make_task_operations
from tlon.operations.time import time_operation_definitions
from tlon.organization.mail.mail import address_for_member
from tlon.organization.mail.store import MailStore
from tlon.sandbox.device import AuditedExecutionDevice, ExecutionDevice
from tlon.sandbox.docker_runtime import AGENT_HOME_CONTAINER_PATH, DockerRuntime
from tlon.sandbox.filesystem import MemberFilesystemService
from tlon.sandbox.shell import MemberShellService
from tlon.tasks.manager import TaskManager

if TYPE_CHECKING:
    from tlon.agents.agent_registry import AgentRegistry
    from tlon.base.config import AgentInfoProvider

logger = get_logger(__name__)

#: The reproduction/evolution tools withheld from probationary members at
#: assembly time (trust ladder, docs/design/unified-member-model.md). The
#: trust *level* is policy (the ``full-member?``
#: hook, ``agents/policy.py``, ADR 0022); this tool subset is the
#: execution-side mechanism the level drives. ``prototype-review`` is
#: deliberately absent: reviewing a staging proposal is advisory (approve
#: stays human).
REPRODUCTION_TOOLS: frozenset[str] = frozenset(
    {"member-spawn", "prototype-write", "behavior-propose", "capability-propose"}
)


def _render_trusted_capability_words(tlon_paths: TlonPaths) -> str:
    """Render the trusted capability library as prompt text for the
    ``capability-propose`` drafting call (anti-duplication + style sample).
    An empty library renders as ``""`` (the tool then omits the section)."""
    lines = []
    for cap in list_trusted_capabilities(tlon_paths):
        if cap.error:
            purity = f"不可用：{cap.error}"
        else:
            purity = "纯" if not cap.effects else "+".join(cap.effects)
        lines.append(f"- {cap.name}（{purity}）：{cap.description}")
    return "\n".join(lines)


def _resolve_context_window(file_config: LLMConfig, model_name: str) -> int:
    """Resolve the model's context window (ADR 0012 phase 2).

    An explicit ``context_window`` in agent.yaml wins (fallback for
    missing/stale registry metadata); otherwise litellm's model registry
    (``get_model_info`` → ``max_input_tokens``); otherwise
    ``DEFAULT_CONTEXT_WINDOW``.
    """
    import litellm

    if file_config.context_window is not None:
        return file_config.context_window
    try:
        max_input_tokens = litellm.get_model_info(model_name).get("max_input_tokens")
        if max_input_tokens:
            return int(max_input_tokens)
    except Exception:
        pass
    logger.info(
        "context_window_fallback",
        model=model_name,
        context_window=DEFAULT_CONTEXT_WINDOW,
    )
    return DEFAULT_CONTEXT_WINDOW


def _build_llm_config(file_config: LLMConfig | None, agent_id: str) -> LLMConfig:
    """Resolve the member's LLM config from its agent.yaml ``llm`` section.

    Member-level only (ADR 0016) — no process-level fallback anywhere:
    ``model_name`` must be declared, and the per-agent knobs
    ``max_tokens`` / ``top_p`` / ``context_window`` pass through
    (context_window None = auto-resolve from litellm's model registry).
    Sampling is never configured — requests carry no temperature, the
    provider default applies. The returned config always carries a
    non-None ``model_name`` and a resolved ``context_window`` int.

    Raises:
        ValueError: the member declares no ``llm.model_name``.
    """
    if file_config is None or file_config.model_name is None:
        raise ValueError(
            f"member '{agent_id}' 未配置模型：在 agent.yaml 的 llm.model_name "
            "声明 provider/model（或经 WebUI 成员编辑设置）"
        )
    model_name = file_config.model_name

    return LLMConfig(
        model_name=model_name,
        max_tokens=file_config.max_tokens,
        top_p=file_config.top_p,
        context_window=_resolve_context_window(file_config, model_name),
        api_base=file_config.api_base,
        embedding_model_name=file_config.embedding_model_name,
    )


async def assemble_member_services(
    config: AgentConfig,
    *,
    registry: "AgentRegistry",
    agent_info: "AgentInfoProvider",
    tlon_paths: TlonPaths,
    metrics_service: MetricsService | None = None,
    trace_store: TraceStore | None = None,
) -> MemberServices:
    """Assemble the capability set for a member.

    Unified member model (ADR 0006): every member gets the full
    capability set — LLM client, memory, task manager, Docker runtime,
    tools, and the team roster. Rules can request a session whenever their
    program requires one; capability availability does not classify a member.
    Resource loading stays on demand. A member declaring
    ``docker.level: L0`` gets no execution device — no DockerRuntime and
    none of the device-dependent shell/filesystem tools; L1 (issue #40,
    文件层) gets the runtime but only the filesystem five — no bash tools,
    and run() is refused at the device layer. Otherwise no
    container exists here: ``run()`` spins a one-shot execution unit per
    command (issue #25) and the file cabinet is created lazily on first
    file I/O via ensure_running(), so waking an agent for pure
    conversation costs zero containers — a daemon failure surfaces as a
    tool error, not a start failure. The runtime is wrapped in
    ``AuditedExecutionDevice`` (issue #21): exec auditing lives on the
    interface boundary.
    """
    # Build infrastructure dependencies
    llm_config = _build_llm_config(config.llm, config.id)
    # agent_id binds the client to the member's token ledger (ADR 0014):
    # every chat() carrying usage appends to {agent_id}/ledger.jsonl.
    llm_client = LLMClient(llm_config, agent_id=config.id, tlon_paths=tlon_paths)

    agent_home = tlon_paths.agent_dir(config.id)
    # Single memory tuning surface: `memory/config.json` per agent
    # (memory/models.py MemoryConfig). The agent_id is left empty here
    # so MemoryManager.load merges the on-disk config; there is no
    # agent.yaml memory section (the old base/config.MemoryConfig is
    # gone — its fields were never consumed).
    memory_config = MemoryConfig()
    write_gate = MemberWriteGate()
    memory = MemoryManager(
        agent_id=config.id,
        config=memory_config,
        write_gate=write_gate,
        data_dir=agent_home,
        llm_config=llm_config,
        tlon_paths=tlon_paths,
    )

    task_manager = TaskManager(
        agent_id=config.id,
        tlon_paths=tlon_paths,
    )

    # Create Docker runtime (shared by shell + filesystem tools), wrapped
    # in the interface-level exec audit (issue #21): every run() emits a
    # device.exec trace span + device_exec log. Tools and
    # MemberServices.docker_runtime both hold the wrapped instance;
    # close() passes through, so MemberRuntime's shutdown path is unchanged.
    # 能力阶梯（issue #26/#40）：L0 无执行装置——不创建 DockerRuntime，
    # 下方也不注册装置工具；L1（文件层）与 L2 都创建 runtime——文件 I/O
    # 需要文件柜容器。L1 的零代码执行是结构性的：bash 工具不注册，
    # run() 在非 L2 由装置层直接拒绝。
    docker_runtime: ExecutionDevice | None = None
    if config.docker.level == "L0":
        logger.info("execution_device_declared_none", agent_id=config.id)
    else:
        try:
            docker_runtime = AuditedExecutionDevice(
                DockerRuntime(config.docker, config.id, tlon_paths), agent_id=config.id
            )
        except Exception as e:
            logger.warning("docker_unavailable", agent_name=config.name, error=str(e))
    # No registry-side container handle is kept (ADR 0006): members
    # are never force-removed — a sleeping member keeps its container
    # (zero-cost wake); a re-assembly reuses it by name.

    # Trust ladder (docs/design/unified-member-model.md): one derivation
    # per assembly — a member's wake
    # re-assembles, so promotion to full takes effect on the next wake
    # (natural in a scale-to-zero world). The level is the society
    # policy's ``full-member?`` hook (ADR 0022); fail-open reads as full
    # trust (availability first). A probationary member (spawned, not yet
    # endorsed, per the default policy) is assembled without the four
    # reproduction/evolution tools (REPRODUCTION_TOOLS; prototype-review
    # stays — advisory only) and gets its mentor (the spawner) cc'd on
    # composed outbound mail.
    is_full = await policy_trust_provider(tlon_paths)(config.id)
    mentor_cc = (
        address_for_member(config.spawned_by)
        if not is_full and config.spawned_by is not None
        else None
    )

    # Create ToolExecutor (direct execution, no EventBus)
    tool_executor = ToolExecutor()

    # Assemble operation definitions from member-owned domain services.
    operations = list(time_operation_definitions())
    shell_service = None
    if config.docker.level != "L0":
        filesystem = MemberFilesystemService(
            config.docker,
            docker_runtime,
            [*config.tool.allowed_paths, AGENT_HOME_CONTAINER_PATH],
            config.tool.allowed_paths,
        )
        operations.extend(make_filesystem_operations(filesystem))
    if config.docker.level == "L2":
        shell_service = MemberShellService(config.docker, docker_runtime)
        operations.extend(make_shell_operations(shell_service))
    proposal_solvency = policy_solvency_provider(tlon_paths)
    near_kin_check = partial(find_near_kin, tlon_paths=tlon_paths)
    operations.extend(
        make_evolution_operations(
            config.id,
            is_full,
            catalog=PrototypeCatalog(lister=partial(list_prototypes, tlon_paths=tlon_paths)),
            staging=StagingCatalog(lister=partial(list_staging_prototypes, tlon_paths=tlon_paths)),
            lineage=PrototypeLineage(lineage=partial(get_prototype_lineage, tlon_paths=tlon_paths)),
            reviewer=PrototypeReviewer(
                reviewer_id=config.id,
                llm_client=llm_client,
                detail=partial(get_prototype_detail, staging=True, tlon_paths=tlon_paths),
                lineage=partial(get_prototype_lineage, tlon_paths=tlon_paths),
                append_review=partial(append_staging_review, tlon_paths=tlon_paths),
            ),
            writer=PrototypeWriter(
                author_id=config.id,
                writer=partial(write_staging_prototype, tlon_paths=tlon_paths),
                prototype=config.prototype,
                solvency=proposal_solvency,
                near_kin=near_kin_check,
            ),
            behavior_proposer=BehaviorProposer(
                agent_id=config.id,
                prototype=config.prototype,
                author_id=config.id,
                llm_client=llm_client,
                memory=memory,
                logs=SessionJournal(config.id, tlon_paths),
                writer=partial(write_staging_prototype, tlon_paths=tlon_paths),
                tlon_paths=tlon_paths,
                solvency=proposal_solvency,
                near_kin=near_kin_check,
            ),
            capability_proposer=CapabilityProposer(
                agent_id=config.id,
                prototype=config.prototype,
                author_id=config.id,
                llm_client=llm_client,
                memory=memory,
                logs=SessionJournal(config.id, tlon_paths),
                writer=partial(write_staging_capability, tlon_paths=tlon_paths),
                tlon_paths=tlon_paths,
                solvency=proposal_solvency,
                trusted_words=partial(_render_trusted_capability_words, tlon_paths),
            ),
        )
    )
    operations.extend(make_memory_operations(memory, SessionJournal(config.id, tlon_paths)))
    operations.extend(make_task_operations(task_manager))
    operations.extend(make_identity_operations(tlon_paths, config.id))
    operations.extend(
        make_observability_operations(
            member_id=config.id,
            mail_stats_provider=mail_stats_provider(tlon_paths),
            reputation_provider=reputation_provider(
                tlon_paths,
                member_ids=lambda: (
                    sorted(agent_info.get_all_ids())
                    + MailStore(tlon_paths.mail_database_file()).known_human_ids()
                ),
            ),
            orphan_provider=orphan_mail_provider(tlon_paths, agent_info=agent_info),
            metrics_snapshot=metrics_service.snapshot if metrics_service else None,
            trace_store=trace_store,
        )
    )
    if not is_full:
        logger.info(
            "probation_tools_withheld",
            agent_name=config.name,
            tools=sorted(REPRODUCTION_TOOLS),
        )
    logger.debug(
        "operations_assembled",
        agent_name=config.name,
        operation_count=len(operations),
    )

    # Read tool timeout from agent config
    per_tool_timeout = config.tool.per_timeout

    return MemberServices(
        # Swap in the merged LLM config so consumers (context pressure,
        # member_runtime.py) see the resolved context_window int.
        config=config.model_copy(update={"llm": llm_config}),
        llm_client=llm_client,
        memory=memory,
        write_gate=write_gate,
        task_manager=task_manager,
        tool_executor=tool_executor,
        tlon_paths=tlon_paths,
        per_tool_timeout=per_tool_timeout,
        registry=registry,
        mentor_cc=mentor_cc,
        docker_runtime=docker_runtime,
        operations=tuple(operations),
        shell_service=shell_service,
        is_full=is_full,
    )
