"""Member service assembly (split from agent_registry).

``assemble_member_services`` builds the services one member receives —
LLM client, task manager, Docker runtime, and the tool list —
and returns it as ``MemberServices`` for ``MemberRuntime``. It is
a pure function of its parameters: the registry injects itself (it
satisfies the ``AgentLifecycle`` port structurally) plus the config
provider and paths, keeping the import direction agents → tools (the
ports live in ``tlon.lifecycle.ports`` / ``tlon.evolution.ports``).
"""

from functools import partial
from typing import TYPE_CHECKING

from tlon.agents.context_builder import ContextBuilder
from tlon.agents.health import orphan_mail_provider
from tlon.agents.mail_stats import mail_stats_provider
from tlon.agents.member_runtime import MemberServices
from tlon.agents.program_package import check_program_sources
from tlon.agents.prototypes.catalog import list_prototypes
from tlon.agents.prototypes.lineage import get_prototype_lineage
from tlon.agents.prototypes.reviews import (
    read_prototype,
    recover_prototype_review,
    submit_prototype_review,
)
from tlon.agents.prototypes.staging import (
    list_staging_prototypes,
    write_staging_prototype,
)
from tlon.agents.reputation import reputation_provider
from tlon.agents.run_journal import RunJournal
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.config import AgentConfig, LLMConfig
from tlon.base.execution import MemberWriteGate
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.metrics import MetricsService
from tlon.base.model_limits import resolve_model_limits
from tlon.base.paths import TlonPaths
from tlon.base.tracing.trace_store import TraceStore
from tlon.evolution.get_lineage import PrototypeLineage
from tlon.evolution.list_prototypes import PrototypeCatalog
from tlon.evolution.list_staging import StagingCatalog
from tlon.evolution.write_prototype import PrototypeWriter
from tlon.operations.context import member_context_operation
from tlon.operations.evolution import make_evolution_operations
from tlon.operations.filesystem import make_filesystem_operations
from tlon.operations.identity import make_identity_operations
from tlon.operations.observability import make_observability_operations
from tlon.operations.runs import make_run_read_operations
from tlon.operations.shell import make_shell_operations
from tlon.operations.tasks import make_task_operations
from tlon.operations.time import time_operation_definitions
from tlon.organization.mail.mail import address_for_member
from tlon.organization.mail.mailbox import Mailbox
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


def _build_llm_config(file_config: LLMConfig | None, agent_id: str) -> LLMConfig:
    """Resolve the member's LLM config from its agent.yaml ``llm`` section.

    Member-level only (ADR 0016) — no process-level fallback anywhere:
    ``model_name`` must be declared, and the per-agent knobs
    ``top_p`` / ``context_window`` pass through
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
    return resolve_model_limits(file_config)


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
    capability set — LLM client, task manager, Docker runtime,
    tools, and the team roster. Rules can start a run whenever their
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

    write_gate = MemberWriteGate()

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
    # 自己的 workspace 文件操作不依赖装置；L1/L2 仍可装配外部文件柜。
    # L1 的零代码执行是结构性的：bash 工具不注册，
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

    # Create ToolExecutor (direct execution, no EventBus)
    tool_executor = ToolExecutor()

    # Assemble operation definitions from member-owned domain services.
    context_reader = ContextBuilder(
        config,
        Mailbox(tlon_paths.mail_database_file(), config.id),
        task_manager,
        address_for_member(config.id),
        RunJournal(config.id, tlon_paths),
        tlon_paths=tlon_paths,
    )
    operations = [
        *time_operation_definitions(),
        member_context_operation(context_reader, tool_executor.list_tools),
    ]
    shell_service = None
    workspace = tlon_paths.agent_workspace_dir(config.id)
    workspace.mkdir(parents=True, exist_ok=True)
    filesystem = MemberFilesystemService(
        config.docker,
        docker_runtime,
        [*config.tool.allowed_paths, AGENT_HOME_CONTAINER_PATH],
        config.tool.allowed_paths,
        workspace=workspace,
    )
    filesystem_operations = make_filesystem_operations(filesystem)
    operations.extend(filesystem_operations)
    if config.docker.level == "L2" and docker_runtime is not None:
        shell_service = MemberShellService(config.docker, docker_runtime)
        operations.extend(make_shell_operations(shell_service))
    operations.extend(
        make_evolution_operations(
            config.id,
            program_checker=check_program_sources,
            catalog=PrototypeCatalog(lister=partial(list_prototypes, tlon_paths=tlon_paths)),
            staging=StagingCatalog(lister=partial(list_staging_prototypes, tlon_paths=tlon_paths)),
            lineage=PrototypeLineage(lineage=partial(get_prototype_lineage, tlon_paths=tlon_paths)),
            prototype_reader=partial(read_prototype, tlon_paths=tlon_paths),
            review_submitter=partial(submit_prototype_review, tlon_paths=tlon_paths),
            review_recoverer=partial(recover_prototype_review, tlon_paths=tlon_paths),
            writer=PrototypeWriter(
                author_id=config.id,
                writer=partial(write_staging_prototype, tlon_paths=tlon_paths),
            ),
        )
    )
    operations.extend(make_run_read_operations(RunJournal(config.id, tlon_paths), config.id))
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
        write_gate=write_gate,
        task_manager=task_manager,
        tool_executor=tool_executor,
        tlon_paths=tlon_paths,
        per_tool_timeout=per_tool_timeout,
        registry=registry,
        docker_runtime=docker_runtime,
        operations=tuple(operations),
        shell_service=shell_service,
    )
