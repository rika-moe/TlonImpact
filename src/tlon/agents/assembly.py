"""Cognitive capability assembly for members (split from agent_registry).

``assemble_cognitive`` builds the full capability set one member gets —
LLM client, memory, task manager, Docker runtime, and the tool list —
and returns it as ``CognitiveCapabilities`` for ``MemberRuntime``. It is
a pure function of its parameters: the registry injects itself (it
satisfies the ``AgentLifecycle`` port structurally) plus the config
provider and paths, keeping the import direction agents → tools (the
ports live in ``tlon.tools.agents.ports``).
"""

from functools import partial
from typing import TYPE_CHECKING

from tlon.agents.capabilities.catalog import list_trusted_capabilities
from tlon.agents.capabilities.staging import write_staging_capability
from tlon.agents.health import orphan_mail_provider
from tlon.agents.mail_inbox import mark_read_actor, unread_mails_provider
from tlon.agents.mail_search import mailbox_mails_provider
from tlon.agents.mail_stats import mail_stats_provider
from tlon.agents.member_runtime import CognitiveCapabilities
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
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.metrics import MetricsService
from tlon.base.paths import TlonPaths
from tlon.base.tracing.trace_store import TraceStore
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.organization.mail.mail import address_for_member
from tlon.sandbox.device import AuditedExecutionDevice, ExecutionDevice
from tlon.sandbox.docker_runtime import AGENT_HOME_CONTAINER_PATH, DockerRuntime
from tlon.tools.agents.contacts import ReadContactsTool, UpdateContactTool
from tlon.tools.agents.list_agents import ListAgentsTool
from tlon.tools.agents.propose_behavior import ProposeBehaviorTool
from tlon.tools.agents.propose_capability import ProposeCapabilityTool
from tlon.tools.agents.spawn_agent import SpawnAgentTool
from tlon.tools.agents.stop_agent import StopAgentTool
from tlon.tools.agents.update_persona import UpdatePersonaTool
from tlon.tools.agents.write_prototype import WritePrototypeTool
from tlon.tools.filesystem.edit_file import EditFileTool
from tlon.tools.filesystem.list_directory import ListDirectoryTool
from tlon.tools.filesystem.read_file import ReadFileTool
from tlon.tools.filesystem.search_files import SearchFilesTool
from tlon.tools.filesystem.write_file import WriteFileTool
from tlon.tools.mail.check_inbox import CheckInboxTool
from tlon.tools.mail.mark_read import MarkReadTool
from tlon.tools.mail.search_mails import SearchMailsTool
from tlon.tools.mail.send_email import SendEmailTool
from tlon.tools.mail.silence import SilenceTool
from tlon.tools.memory.forget_memory import ForgetMemoryTool
from tlon.tools.memory.read_session import ReadSessionTool
from tlon.tools.memory.remember_fact import RememberFactTool
from tlon.tools.memory.search_memories import SearchMemoriesTool
from tlon.tools.observability.get_metrics import GetMetricsTool
from tlon.tools.observability.mail_stats import GetMailStatsTool
from tlon.tools.observability.reputation import GetReputationTool
from tlon.tools.observability.social_health import GetSocialHealthTool
from tlon.tools.observability.traces import GetTraceTool, ListTracesTool
from tlon.tools.prototypes.get_lineage import GetPrototypeLineageTool
from tlon.tools.prototypes.list_prototypes import ListPrototypesTool
from tlon.tools.prototypes.list_staging import ListStagingPrototypesTool
from tlon.tools.prototypes.review_prototype import ReviewPrototypeTool
from tlon.tools.shell.execute_bash import CheckBashTaskTool, ExecuteBashTool
from tlon.tools.system.current_time import GetCurrentTimeTool
from tlon.tools.tasks.complete_task import CompleteTaskTool
from tlon.tools.tasks.create_task import CreateTaskTool
from tlon.tools.tasks.get_current_task import GetCurrentTaskTool
from tlon.tools.tasks.list_tasks import ListTasksTool
from tlon.tools.tasks.manager import TaskManager
from tlon.tools.tasks.pause_task import PauseTaskTool
from tlon.tools.tasks.remove_task import RemoveTaskTool
from tlon.tools.tasks.start_task import StartTaskTool

if TYPE_CHECKING:
    from tlon.agents.agent_registry import AgentRegistry
    from tlon.base.config import AgentInfoProvider

logger = get_logger(__name__)

#: The reproduction/evolution tools withheld from probationary members at
#: assembly time (trust ladder, docs/design/unified-member-model.md). The
#: trust *level* is policy (the ``full-member?``
#: hook, ``agents/policy.py``, ADR 0022); this tool subset is the
#: execution-side mechanism the level drives. ``reviewPrototype`` is
#: deliberately absent: reviewing a staging proposal is advisory (approve
#: stays human).
REPRODUCTION_TOOLS: frozenset[str] = frozenset(
    {"spawnAgent", "stopAgent", "writePrototype", "proposeBehavior", "proposeCapability"}
)


def _render_trusted_capability_words(tlon_paths: TlonPaths) -> str:
    """Render the trusted capability library as prompt text for the
    ``proposeCapability`` drafting call (anti-duplication + style sample).
    An empty library renders as ``""`` (the tool then omits the section)."""
    lines = []
    for cap in list_trusted_capabilities(tlon_paths):
        if cap.pure is True:
            purity = "纯"
        elif cap.pure is False:
            purity = "非纯"
        else:
            purity = "纯度未知"
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


async def assemble_cognitive(
    config: AgentConfig,
    *,
    registry: "AgentRegistry",
    agent_info: "AgentInfoProvider",
    tlon_paths: TlonPaths,
    metrics_service: MetricsService | None = None,
    trace_store: TraceStore | None = None,
) -> CognitiveCapabilities:
    """Assemble the capability set for a member.

    Unified member model (ADR 0006): every member gets the full
    capability set — LLM client, memory, task manager, Docker runtime,
    tools, and the team roster. Whether the cognitive engine (loop /
    scheduler) is actually attached is derived from the spec inside
    ``MemberRuntime``; a member without a loop rule simply never uses
    these objects (lazy assembly: objects are cheap, loading is on
    demand). Exception (issue #26): a member declaring
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
    memory = MemoryManager(
        agent_id=config.id,
        config=memory_config,
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
    # CognitiveCapabilities.docker_runtime both hold the wrapped instance;
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
    # endorsed, per the default policy) is assembled without the five
    # reproduction/evolution tools (REPRODUCTION_TOOLS; reviewPrototype
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

    # Register tools
    # Device-dependent tools, gated by the capability ladder
    # (issue #26/#40): L2 registers the shell pair plus the filesystem five;
    # L1 (文件层) registers the filesystem five only — 结构性零代码执行;
    # L0 registers none. Note the gate is the declared level, not
    # docker_runtime: an L1/L2 member whose daemon is unreachable keeps
    # the tools (they fail-soft with "Docker runtime not initialized") —
    # 声明即数据.
    if config.docker.level == "L2":
        execute_bash_tool = ExecuteBashTool(config.docker, docker_runtime)
        tool_executor.register_tool(execute_bash_tool)
        # checkBashTask shares this agent's task table: no cross-agent visibility.
        tool_executor.register_tool(CheckBashTaskTool(execute_bash_tool.background_tasks))
    if config.docker.level != "L0":
        allowed_paths = config.tool.allowed_paths
        # Read-side tools additionally see the agent's own home (behavior spec,
        # memory, mailbox) at the read-only mount (docs/design/execution-device.md:
        # 行为即数据 —
        # self-awareness is the precondition of self-evolution). Write/edit keep
        # the configured whitelist: behavior changes go through the prototype
        # channel, never direct file writes.
        read_allowed_paths = [*allowed_paths, AGENT_HOME_CONTAINER_PATH]
        tool_executor.register_tool(ReadFileTool(config.docker, docker_runtime, read_allowed_paths))
        tool_executor.register_tool(WriteFileTool(config.docker, docker_runtime, allowed_paths))
        tool_executor.register_tool(EditFileTool(config.docker, docker_runtime, allowed_paths))
        tool_executor.register_tool(
            ListDirectoryTool(config.docker, docker_runtime, read_allowed_paths)
        )
        tool_executor.register_tool(
            SearchFilesTool(config.docker, docker_runtime, read_allowed_paths)
        )
    tool_executor.register_tool(GetCurrentTimeTool())
    tool_executor.register_tool(SendEmailTool())
    tool_executor.register_tool(SilenceTool())
    # Persona self-edit (ADR 0024, docs/design/identity.md): the member
    # rewrites its own IDENTITY.md / SOUL.md on the host (outside the
    # container /workspace). agent_id binds at construction — touching
    # another member's persona files is structurally impossible. Not a
    # reproduction/evolution tool: registered unconditionally (probationary
    # members included).
    tool_executor.register_tool(UpdatePersonaTool(tlon_paths=tlon_paths, agent_id=config.id))
    # Contact book (ADR 0024, docs/design/identity.md): the member's
    # private CONTACTS.md — full-text read + entry-level upsert. Same
    # host-side binding as updatePersona (agent_id at construction —
    # structurally private); not reproduction/evolution, registered
    # unconditionally.
    tool_executor.register_tool(ReadContactsTool(tlon_paths=tlon_paths, agent_id=config.id))
    tool_executor.register_tool(UpdateContactTool(tlon_paths=tlon_paths, agent_id=config.id))
    # searchMails (docs/design/skills.md): read-only structured query over
    # the agent's own
    # mailbox — the mail evidence source of the explain-decision skill.
    tool_executor.register_tool(
        SearchMailsTool(provider=mailbox_mails_provider(tlon_paths), agent_id=config.id)
    )
    # checkInbox / markRead (ADR 0028 收件箱驱动模型): the agent pulls its
    # own unread mail and manages the read cursor explicitly — delivery no
    # longer injects mail bodies into the session prompt. agent_id binds at
    # construction: only the owning member's mailbox is reachable.
    tool_executor.register_tool(
        CheckInboxTool(provider=unread_mails_provider(tlon_paths), agent_id=config.id)
    )
    tool_executor.register_tool(
        MarkReadTool(actor=mark_read_actor(tlon_paths), agent_id=config.id)
    )
    tool_executor.register_tool(
        ListPrototypesTool(lister=partial(list_prototypes, tlon_paths=tlon_paths))
    )
    # Staging proposals (docs/design/prototypes.md): agent-authored
    # prototypes awaiting human
    # trust approval — visible so a member (e.g. the scribe's digest) can
    # surface them as pending human decisions.
    tool_executor.register_tool(
        ListStagingPrototypesTool(lister=partial(list_staging_prototypes, tlon_paths=tlon_paths))
    )
    # Lineage (docs/design/prototypes.md): the prototype genealogy is
    # first-class data — any
    # member can walk a :parent chain and see each generation's diff.
    tool_executor.register_tool(
        GetPrototypeLineageTool(lineage=partial(get_prototype_lineage, tlon_paths=tlon_paths))
    )
    # Heterogeneous review (docs/design/prototypes.md, phase 2): a member
    # reviews a staging
    # proposal with its own llm_client — the heterogeneity comes from the
    # reviewer's model bloodline (config.model_name), the verdict is never
    # caller-supplied. Records append to the proposal's REVIEW.md and ride
    # along with approval's whole-directory move. Advisory only: approve
    # stays a human decision.
    tool_executor.register_tool(
        ReviewPrototypeTool(
            reviewer_id=config.id,
            llm_client=llm_client,
            detail=partial(get_prototype_detail, staging=True, tlon_paths=tlon_paths),
            lineage=partial(get_prototype_lineage, tlon_paths=tlon_paths),
            append_review=partial(append_staging_review, tlon_paths=tlon_paths),
        )
    )
    # Spawn/stop lifecycle tools (stage 6b): the registry is the
    # assembler, so it injects itself (it satisfies the AgentLifecycle
    # port structurally); spawner_id binds the tool to the owning
    # agent for budget accounting and audit. Trust ladder
    # (docs/design/unified-member-model.md):
    # probationary members don't get these (REPRODUCTION_TOOLS) — the
    # DSL spawn action is backstopped by the SpawnManager probation
    # quota (same funnel); the DSL stop action is not gated (known gap:
    # the stop-request policy gate is Adam-side / future work).
    if is_full:
        tool_executor.register_tool(SpawnAgentTool(registry=registry, spawner_id=config.id))
        tool_executor.register_tool(StopAgentTool(registry=registry, spawner_id=config.id))
    tool_executor.register_tool(ListAgentsTool(registry=registry))
    # Spec authoring (stage 4a): writes untrusted prototypes to the
    # staging area; author_id is recorded as the :author for audit.
    # Proposal solvency gate (docs/design/prototypes.md, agent side only
    # — the human direct
    # write channel is never gated): an insolvent bloodline loses
    # proposal eligibility until it earns endorsements back. The gate
    # recomputes from raw files per call (proposals are low-frequency).
    proposal_solvency = policy_solvency_provider(tlon_paths)
    # Near-kin rejection (docs/design/prototypes.md, agent side only like
    # the solvency gate):
    # reskin-level duplicates of an existing prototype (trusted or
    # staging) are turned away before they reach disk.
    near_kin_check = partial(find_near_kin, tlon_paths=tlon_paths)
    # Trust ladder (docs/design/unified-member-model.md): both
    # spec-authoring tools are reproduction/
    # evolution tools (REPRODUCTION_TOOLS) — withheld from probationary
    # members.
    if is_full:
        tool_executor.register_tool(
            WritePrototypeTool(
                author_id=config.id,
                writer=write_staging_prototype,
                prototype=config.prototype,
                solvency=proposal_solvency,
                near_kin=near_kin_check,
            )
        )
    # Experience entry of the generation channel
    # (docs/design/unified-member-model.md): drafts the next
    # generation's spec from the member's own behavior + memory, parented
    # to its own prototype. First agents-domain tool to receive the
    # llm_client / memory pair; the journal (agents layer) is injected as
    # the SessionLogReader port so tlon.tools never imports tlon.agents.
    if is_full:
        tool_executor.register_tool(
            ProposeBehaviorTool(
                agent_id=config.id,
                prototype=config.prototype,
                author_id=config.id,
                llm_client=llm_client,
                memory=memory,
                logs=SessionJournal(config.id, tlon_paths),
                writer=write_staging_prototype,
                tlon_paths=tlon_paths,
                solvency=proposal_solvency,
                near_kin=near_kin_check,
            )
        )
    # Experience entry of the capability channel (issue #30,
    # docs/design/capabilities.md): drafts a capability word's tlosure
    # artifact from the member's own memory + lessons, through the
    # write_staging_capability gate. No near-kin check (unlike
    # proposeBehavior): the vocabulary is a few dozen words, same-name is
    # a hard structural rejection, and reviewers see the full artifact
    # plus its :examples assertions — a similarity subsystem would be a
    # preventive abstraction. The solvency gate is reused (proposal
    # eligibility follows bloodline solvency). The trusted-library
    # listing is rendered here (agents side) and injected as a zero-arg
    # callable, keeping tlon.tools free of tlon.agents.
    if is_full:
        tool_executor.register_tool(
            ProposeCapabilityTool(
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
            )
        )
    tool_executor.register_tool(RememberFactTool(memory))
    tool_executor.register_tool(SearchMemoriesTool(memory))
    tool_executor.register_tool(ForgetMemoryTool(memory))
    # Claim → source read-back (ADR 0017 stage 4): searchMemories exposes
    # source_session_ids; this tool reads the referenced session's journal.
    # Same SessionLogReader port as proposeBehavior — tlon.tools stays
    # free of tlon.agents.
    tool_executor.register_tool(ReadSessionTool(SessionJournal(config.id, tlon_paths)))
    tool_executor.register_tool(CreateTaskTool(task_manager))
    tool_executor.register_tool(ListTasksTool(task_manager))
    tool_executor.register_tool(StartTaskTool(task_manager))
    tool_executor.register_tool(PauseTaskTool(task_manager))
    tool_executor.register_tool(CompleteTaskTool(task_manager))
    tool_executor.register_tool(RemoveTaskTool(task_manager))
    tool_executor.register_tool(GetCurrentTaskTool(task_manager))
    # Observability (read-only): the agent can read the society's
    # health report but never write it. Metrics/traces pillars are
    # optional here so tests can assemble without the OTel pipeline; the
    # server always injects both.
    tool_executor.register_tool(
        GetMailStatsTool(provider=mail_stats_provider(tlon_paths), agent_id=config.id)
    )
    # Reputation (docs/design/mail.md): derived, never stored — the member
    # enumeration
    # is re-read per query so the report always sees the current roster.
    tool_executor.register_tool(
        GetReputationTool(
            provider=reputation_provider(
                tlon_paths,
                member_ids=lambda: sorted(agent_info.get_all_ids()) + tlon_paths.known_human_ids(),
            ),
            agent_id=config.id,
        )
    )
    # Social health (docs/design/mail.md): the orphan-mail scan is the
    # same collector the
    # PublicHealthMonitor publishes as MAIL_ORPHAN_SCAN, here on demand;
    # the roster is re-read per query so the report sees the current
    # society.
    tool_executor.register_tool(
        GetSocialHealthTool(
            provider=orphan_mail_provider(
                tlon_paths,
                member_ids=lambda: sorted(agent_info.get_all_ids()) + tlon_paths.known_human_ids(),
            )
        )
    )
    if metrics_service is not None:
        tool_executor.register_tool(GetMetricsTool(snapshot=metrics_service.snapshot))
    if trace_store is not None:
        tool_executor.register_tool(ListTracesTool(trace_store=trace_store, agent_id=config.id))
        tool_executor.register_tool(GetTraceTool(trace_store=trace_store))
    if not is_full:
        logger.info(
            "probation_tools_withheld",
            agent_name=config.name,
            tools=sorted(REPRODUCTION_TOOLS),
        )
    logger.debug(
        "tools_registered",
        agent_name=config.name,
        tool_count=len(tool_executor.list_tools()),
    )

    # Initialize tools
    await tool_executor.initialize()

    # Read tool timeout from agent config
    per_tool_timeout = config.tool.per_timeout

    return CognitiveCapabilities(
        # Swap in the merged LLM config so consumers (context pressure,
        # member_runtime.py) see the resolved context_window int.
        config=config.model_copy(update={"llm": llm_config}),
        llm_client=llm_client,
        memory=memory,
        task_manager=task_manager,
        tool_executor=tool_executor,
        tlon_paths=tlon_paths,
        per_tool_timeout=per_tool_timeout,
        registry=registry,
        mentor_cc=mentor_cc,
        docker_runtime=docker_runtime,
    )
