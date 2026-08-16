"""Agent Registry for managing multiple agents.

The registry is pure mechanism (docs/design/members.md, 阶段 D — the
policy/mechanism split):
- Agent assembly and run table (``_start_agent`` / shutdown)
- Execution of lifecycle requests: it subscribes to AGENT_START_REQUEST /
  AGENT_STOP_REQUEST and fulfills them idempotently (wake locks,
  ``_waking`` dedup, run-state re-validation)
- Idle *fact* detection: the monitor publishes AGENT_IDLE for lazy agents
  (no ``:on :boot`` rule, or contractors) idle past their timeout with no
  live sessions — deciding what to do with that fact is not the
  registry's job
- Spawn/stop of contractors (stage 6b): prototype instantiation under a
  spawn budget (global activation cap, spawn depth, per-session quota);
  agent-spawned contractors connect to the registry directly (= fork)

Policy — which agents should run — lives in Adam (tlon.agents.adam), the
built-in init member: boot set and idle drain are its two decision lines.
Wake-on-mail is not a policy line any more (phase E): the registry
*derives* it from each member's spec — a sleeping recipient whose spec
declares ``:on :mail-delivered`` is woken. Lifecycle membership is
spec-declared: the boot set and the lazy set come from each member's
``:on`` rules (the spec is the single authority). The registry never
imports Adam.

Configuration is delegated to AgentInfoProvider (typically ConfigManager).

Architecture:
    AgentRegistry is a Component that depends on:
    - EventBus (for agent event publishing and request subscriptions)
    - AgentInfoProvider (for configuration queries)

    All dependencies are injected via constructor (DAG rule).
"""

import asyncio
import time
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING, Any, NoReturn

from tlon.agents.eval import ScriptHostQueries, make_member_builtins
from tlon.agents.lifecycle import StartRequest, StopRequest
from tlon.agents.loader import load_member_spec
from tlon.agents.member_runtime import CognitiveCapabilities, MemberRuntime
from tlon.agents.member_spec import MemberSpec
from tlon.agents.prototypes import (
    get_prototype_info,
    instantiate,
    list_prototypes,
    write_staging_prototype,
)
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.component import Component
from tlon.base.config import AgentConfig, LLMConfig, yaml_save
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths, paths
from tlon.base.settings import Settings
from tlon.base.tracing.trace_context import continue_trace, start_span
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.organization.mail.mailbox import Mailbox
from tlon.sandbox.docker_runtime import DockerRuntime
from tlon.tools.agents.list_agents import ListAgentsTool
from tlon.tools.agents.ports import SpawnRejectedError, StopRejectedError
from tlon.tools.agents.spawn_agent import SpawnAgentTool
from tlon.tools.agents.stop_agent import StopAgentTool
from tlon.tools.agents.write_prototype import WritePrototypeTool
from tlon.tools.filesystem.edit_file import EditFileTool
from tlon.tools.filesystem.read_file import ReadFileTool
from tlon.tools.filesystem.write_file import WriteFileTool
from tlon.tools.mail.send_email import SendEmailTool
from tlon.tools.memory.compact import CompactTool
from tlon.tools.memory.forget_memory import ForgetMemoryTool
from tlon.tools.memory.remember_fact import RememberFactTool
from tlon.tools.memory.search_memories import SearchMemoriesTool
from tlon.tools.prototypes.list_prototypes import ListPrototypesTool
from tlon.tools.shell.execute_bash import CheckBashTaskTool, ExecuteBashTool
from tlon.tools.tasks.complete_task import CompleteTaskTool
from tlon.tools.tasks.create_task import CreateTaskTool
from tlon.tools.tasks.get_current_task import GetCurrentTaskTool
from tlon.tools.tasks.list_tasks import ListTasksTool
from tlon.tools.tasks.manager import TaskManager
from tlon.tools.tasks.pause_task import PauseTaskTool
from tlon.tools.tasks.remove_task import RemoveTaskTool
from tlon.tools.tasks.start_task import StartTaskTool

if TYPE_CHECKING:
    from tlon.base.config import AgentInfoProvider

logger = get_logger(__name__)

# Quota bucket used when no session context is available (e.g. direct calls
# from tests): all such spawns share one bucket per spawner.
_UNKNOWN_SESSION = "-"


def _build_llm_config(file_config: LLMConfig | None, settings: Settings) -> LLMConfig:
    """Merge LLM config from YAML file with runtime settings.

    File config provides model parameters (model_name, temperature, etc.)
    Settings provide runtime parameters (base_url, auth_token, etc.)

    Args:
        file_config: LLMConfig from agent YAML (may be None)
        settings: Application settings from environment variables

    Returns:
        Merged LLMConfig with all fields populated
    """
    if file_config is None:
        file_config = LLMConfig()

    return LLMConfig(
        model_name=file_config.model_name,
        temperature=file_config.temperature,
        max_tokens=file_config.max_tokens,
        top_p=file_config.top_p,
        base_url=settings.llm_base_url,
        auth_token=settings.llm_api_key,
        max_retries=settings.llm_max_retries,
        base_delay=settings.llm_base_delay,
    )


class RegistryQueries(ScriptHostQueries):
    """The shared ``ScriptHostQueries`` implementation over the registry.

    Used by registry-spawned script members (P5b) and by Adam
    (``agents/adam.py``), which imports it from here — Adam depends on the
    registry, never the reverse (kernel first, PID 1 second; the DAG stays
    acyclic). Explicitly implements the protocol so signature drift fails
    at this definition site. Non-string input safely reads as False.
    """

    def __init__(self, registry: "AgentRegistry"):
        self._registry = registry

    def is_sleeping(self, agent_id: Any) -> bool:
        """Registered (config known) but not running."""
        if not isinstance(agent_id, str) or not agent_id:
            return False
        return (
            self._registry.get_agent(agent_id) is None
            and self._registry.get_config(agent_id) is not None
        )

    def is_running(self, agent_id: Any) -> bool:
        """Currently in the run table."""
        return isinstance(agent_id, str) and self._registry.get_agent(agent_id) is not None

    def is_lazy(self, agent_id: Any) -> bool:
        """A member that can sleep / be drained (phase E: spec-derived)."""
        if not isinstance(agent_id, str):
            return False
        return self._registry.is_lazy(agent_id)

    def start_all_candidates(self) -> list[str]:
        """The boot set: members whose spec declares ``:on :boot`` (phase E)."""
        return self._registry.boot_candidates()


class AgentRegistry(Component):
    """Registry for managing multiple agents — pure mechanism.

    Activation-aware run state (see docs/design/members.md, 阶段 D):
    - The registry owns the run table, per-agent wake locks, and the
      ``_waking`` dedup set, and executes AGENT_START_REQUEST /
      AGENT_STOP_REQUEST idempotently.
    - "lazy" agents sleep until a start request arrives and are drained
      after `idle_timeout_seconds` of inactivity with no live sessions;
      idle contractors are discarded entirely, home directory included.
    - Which requests get issued is Adam's policy, not the registry's.

    Configuration is provided by AgentInfoProvider (typically ConfigManager),
    allowing this class to focus on agent lifecycle management only.

    Dependencies (constructor injected):
        - event_bus: EventBus for agent event publishing
        - agent_info_provider: AgentInfoProvider for configuration queries

    Example:
        >>> registry = AgentRegistry(
        ...     event_bus=event_bus,
        ...     agent_info_provider=config_manager,
        ... )
        >>> registry.register("dev-001")  # Register by ID
        >>> await registry.initialize()  # Subscribe + start idle monitor
        >>> agent = registry.get_agent("dev-001")  # Get running agent
    """

    # Idle monitor check period (seconds)
    _IDLE_CHECK_INTERVAL_SECONDS = 30

    # Per-agent assembly timeout (seconds): a hung start must not wedge
    # the agent's wake lock forever (restores the old boot-loop guard).
    _START_TIMEOUT_SECONDS = 60

    def __init__(
        self,
        event_bus: EventBus,
        agent_info_provider: "AgentInfoProvider",
        paths: "TlonPaths | None" = None,
        max_activations: int = 8,
        max_spawn_depth: int = 3,
        session_spawn_quota: int = 2,
        contractor_idle_timeout_seconds: int = 300,
    ):
        """Initialize the agent registry.

        All dependencies are injected via constructor (DAG architecture).

        Args:
            event_bus: Event bus for mail delivery and lifecycle events
            agent_info_provider: Provider for agent configuration queries
            paths: TlonPaths instance (defaults to module singleton)
            max_activations: spawn budget — cap on running agents total
                (residents included); spawning beyond it is rejected
            max_spawn_depth: spawn budget — a contractor's depth is
                spawner.depth + 1; exceeding this rejects the spawn
            session_spawn_quota: spawn budget — max spawns per
                (spawner, session) pair
            contractor_idle_timeout_seconds: idle drain timeout assigned to
                spawned contractors (shorter than the resident default —
                contractors are disposable)
        """
        super().__init__(name="AgentRegistry")
        self.event_bus = event_bus
        self._agent_info = agent_info_provider
        self._paths = paths  # None means use module singleton

        # Spawn budget (stage 6b). The quota ledger lives in memory only —
        # a restart zeroes it, which is acceptable for an anti-runaway cap.
        self._max_activations = max_activations
        self._max_spawn_depth = max_spawn_depth
        self._session_spawn_quota = session_spawn_quota
        self._contractor_idle_timeout_seconds = contractor_idle_timeout_seconds
        # (spawner_id, session_id) -> successful spawn count
        self._spawn_quota: dict[tuple[str, str], int] = {}

        # Registered agent IDs (persona full set; may or may not be running)
        self._registered_ids: list[str] = []

        # Running agents (activations): one unified member runtime (react
        # or deterministic by the derived member_type) — all expose
        # shutdown / get_state / active_session_count / idle_seconds
        self._agents: dict[str, MemberRuntime] = {}

        # Docker runtimes built during cognitive assembly, keyed by agent
        # id — the registry-side handle used to reclaim a contractor's
        # container on discard (). Resident sleeps and server shutdown
        # deliberately keep their containers (zero-cost wake / lazy reuse
        # on next boot), so entries are only consumed by
        # ``_discard_contractor``; a re-assembly overwrites the entry.
        self._docker_runtimes: dict[str, DockerRuntime] = {}

        # Request execution state: agents currently being started, and one
        # lock per agent serializing start/drain of the same persona
        self._waking: set[str] = set()
        self._wake_locks: dict[str, asyncio.Lock] = {}

        # Lifecycle request subscriptions + idle monitor task
        self._request_subscriptions: list[str] = []
        self._idle_monitor_task: asyncio.Task[None] | None = None

        # Spec-derived lifecycle profile cache (phase E): per registered
        # member, the loaded MemberSpec or None (unreadable). The boot set,
        # the lazy set, and wake eligibility all derive from here — the
        # spec is the single lifecycle authority. None is cached too so a
        # broken spec is read once, not on every boot/status/wake query;
        # adopt/reload invalidates the entry to retry.
        self._profiles: dict[str, MemberSpec | None] = {}

        # Host builtins validated against — shared by the profile loader and
        # ``_start_agent`` so a member's spec passes the same gate whether
        # loaded for derivation or for assembly.
        self._host_builtins = make_member_builtins(RegistryQueries(self))

        logger.info("agent_registry_constructed")

    def register(self, agent_id: str) -> None:
        """Register an agent to be started.

        The agent configuration is looked up from AgentInfoProvider.
        The agent will be started during initialize().

        Args:
            agent_id: The agent ID to register

        Raises:
            ValueError: If agent configuration is not found
        """
        if not self._agent_info.get_config(agent_id):
            raise ValueError(f"Cannot register unknown agent: {agent_id}")

        if agent_id not in self._registered_ids:
            self._registered_ids.append(agent_id)
            logger.info("agent_registered", agent_id=agent_id)

    def register_all(self, agent_ids: list[str]) -> None:
        """Register multiple agents.

        Args:
            agent_ids: List of agent IDs
        """
        for agent_id in agent_ids:
            self.register(agent_id)

    # ==================== Spec-derived lifecycle (phase E) ====================

    def _spec_profile(self, agent_id: str) -> MemberSpec | None:
        """Load (and cache) a member's validated spec, or None if unreadable.

        The profile is the source of boot-set, lazy-set, and wake
        eligibility. It is validated against the same host builtins as
        ``_start_agent`` so a spec that uses host predicates (``sleeping?``
        etc.) derives consistently. A failed load is cached as None with a
        single warning — the entry is invalidated by adopt/reload so a fixed
        file is retried.
        """
        cached = self._profiles.get(agent_id)
        if cached is not None:
            return cached
        if agent_id in self._profiles:
            return None  # cached failure
        try:
            spec = load_member_spec(agent_id, builtins=self._host_builtins)
        except (FileNotFoundError, ValueError, OSError) as e:
            logger.warning("member_spec_unreadable", agent_id=agent_id, error=str(e))
            self._profiles[agent_id] = None
            return None
        self._profiles[agent_id] = spec
        return spec

    def boot_candidates(self) -> list[str]:
        """The boot set (phase E): registered members whose spec declares
        ``:on :boot``, not already running.

        A member whose spec cannot be loaded has no boot rule to declare and
        is excluded — the spec is the single lifecycle authority.
        """
        candidates: list[str] = []
        for agent_id in self._registered_ids:
            spec = self._spec_profile(agent_id)
            if spec is None or not any(rule.on is None for rule in spec.rules):
                continue
            if self._agents.get(agent_id) is not None:
                continue
            candidates.append(agent_id)
        return candidates

    def is_lazy(self, agent_id: str) -> bool:
        """A member that can sleep / be drained (phase E: spec-derived).

        Contractors are always lazy (disposable, idle-reaped). Otherwise a
        member is lazy iff its spec has no ``:on :boot`` rule — or its spec
        cannot be loaded, in which case it is inert (registered only).
        """
        config = self._agent_info.get_config(agent_id)
        if config is not None and config.role == "contractor":
            return True
        spec = self._spec_profile(agent_id)
        if spec is None:
            return True
        return not any(rule.on is None for rule in spec.rules)

    async def initialize(self) -> None:
        """Initialize the registry: subscribe to requests, start the monitor.

        Pure mechanism: no agent is started here. Wake-on-mail is now a
        mechanized policy — the registry derives wake eligibility from each
        member's spec (``:on :mail-delivered``) and issues its own
        AGENT_START_REQUEST{reason="wake"}; boot remains Adam's policy (it
        issues AGENT_START_REQUEST after its own initialize, asynchronous
        over the bus). Agents that fail to start are logged by the request
        fulfillment path — the server continues operating with the agents
        that did start.
        """
        logger.info("agent_registry_initializing", agent_ids=self._registered_ids)

        # Lifecycle request subscriptions (idempotent across re-initialize)
        if not self._request_subscriptions:
            self._request_subscriptions.append(
                self.event_bus.subscribe(
                    EventType.AGENT_START_REQUEST, self._on_start_request
                )
            )
            self._request_subscriptions.append(
                self.event_bus.subscribe(
                    EventType.AGENT_STOP_REQUEST, self._on_stop_request
                )
            )
            self._request_subscriptions.append(
                self.event_bus.subscribe(
                    EventType.MAIL_DELIVERED, self._on_mail_delivered
                )
            )

        # Idle fact monitor (idempotent across re-initialize)
        if self._idle_monitor_task is None:
            self._idle_monitor_task = asyncio.create_task(self._idle_monitor_loop())

        self._initialized = True
        logger.info(
            "agent_registry_initialized",
            active_count=len(self._agents),
            registered_ids=self._registered_ids,
        )

    async def shutdown(self) -> None:
        """Shutdown all agents and cleanup."""
        logger.info("agent_registry_shutdown_started")

        # Stop the idle monitor first so it cannot drain mid-shutdown
        if self._idle_monitor_task:
            self._idle_monitor_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._idle_monitor_task
            self._idle_monitor_task = None

        # Stop lifecycle request subscriptions
        for subscription in self._request_subscriptions:
            self.event_bus.unsubscribe(subscription)
        self._request_subscriptions.clear()

        # Shutdown all active agents
        for agent_id, agent in list(self._agents.items()):
            try:
                await agent.shutdown()
                logger.info("agent_shutdown", agent_id=agent_id)
            except Exception as e:
                logger.error("agent_shutdown_failed", agent_id=agent_id, error=str(e))

        self._agents.clear()
        self._registered_ids.clear()
        self._waking.clear()
        self._spawn_quota.clear()
        self._profiles.clear()  # phase E: spec-derived lifecycle profiles
        # Containers outlive the server by design (lazy reuse on next
        # boot) — only the handles are dropped.
        self._docker_runtimes.clear()
        self._initialized = False

        logger.info("agent_registry_shutdown_completed")

    async def _start_agent(self, agent_id: str) -> MemberRuntime | None:
        """Assemble and start one agent (phase C: no runtime fork).

        This is the sole assembler for members and their infrastructure.
        The behavior spec is total and derives the member type: a spec with
        a cognitive rule gets cognitive capabilities attached
        (``_assemble_cognitive``), a deterministic spec builds none — both
        become the same MemberRuntime.

        Args:
            agent_id: ID of the agent to start

        Returns:
            The started MemberRuntime instance, or None if not found
        """
        if agent_id in self._agents:
            # Already active
            return self._agents[agent_id]

        config = self._agent_info.get_config(agent_id)
        if not config:
            logger.error("unknown_agent", agent_id=agent_id)
            return None

        try:
            logger.info("agent_starting", agent_id=agent_id, agent_name=config.name)

            # Unified assembly (phase C): the spec is total and derives the
            # member type — a cognitive rule gets the cognitive engine
            # attached, a deterministic spec builds no LLM/memory/tool
            # infrastructure at all. Host builtins gate validation too
            # (D6.6: host predicates open to registry members).
            builtins = self._host_builtins
            spec = load_member_spec(agent_id, builtins=builtins)
            self._profiles[agent_id] = spec  # cache the profile (phase E)
            mailbox = Mailbox((self._paths or paths).member_mail_dir(config.id))
            cognitive = (
                await self._assemble_cognitive(config, mailbox)
                if any(rule.loop is not None for rule in spec.rules)
                else None
            )

            member = MemberRuntime(
                member_id=config.id,
                name=config.name,
                event_bus=self.event_bus,
                spec=spec,
                builtins=builtins,
                mailbox=mailbox,
                cognitive=cognitive,
                registry=self,
            )
            await member.initialize()

            # Track active agent
            self._agents[agent_id] = member

            # Publish agent started event (runtime derived from the spec)
            await self.event_bus.publish(
                Event(
                    type=EventType.AGENT_STARTED,
                    payload={
                        "agent_id": agent_id,
                        "name": config.name,
                        "runtime": member.member_type,
                        "spec_hash": spec.source_hash,
                    },
                    source="agent_registry",
                )
            )

            logger.info(
                "agent_started",
                agent_id=agent_id,
                agent_name=config.name,
                spec_hash=spec.source_hash,
                runtime=member.member_type,
            )
            return member

        except Exception as e:
            logger.error("agent_start_failed", agent_id=agent_id, error=str(e), exc_info=True)
            return None

    async def _assemble_cognitive(
        self, config: AgentConfig, mailbox: Mailbox
    ) -> CognitiveCapabilities:
        """Assemble the cognitive capabilities for a member with a loop rule.

        Everything the cognitive engine needs beyond the deterministic
        core: LLM client, memory, task manager, Docker runtime, tools, and
        the team roster. Deterministic members build none of this
        (D1: cognitive=None). The container is NOT started here:
        DockerRuntime lazily calls ensure_running() on first
        execute/read/write, so waking an agent for pure conversation costs
        zero containers — a daemon failure surfaces as a tool error, not a
        start failure.
        """
        from tlon.base.settings import get_settings

        # Build infrastructure dependencies
        llm_config = _build_llm_config(config.llm, get_settings())
        llm_client = LLMClient(llm_config)

        agent_home = (self._paths or paths).agent_dir(config.id)
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
        )

        task_manager = TaskManager(
            agent_id=config.id,
            data_dir=str(agent_home),
        )

        # Create Docker runtime (shared by shell + filesystem tools).
        docker_runtime: DockerRuntime | None = None
        try:
            docker_runtime = DockerRuntime(config.docker, config.id)
        except Exception as e:
            logger.warning("docker_unavailable", agent_name=config.name, error=str(e))
        # Registry-side handle for container reclaim on contractor
        # discard (); a daemon failure leaves no entry to reclaim.
        if docker_runtime is not None:
            self._docker_runtimes[config.id] = docker_runtime

        # Create ToolExecutor (direct execution, no EventBus)
        tool_executor = ToolExecutor()

        # Register tools
        execute_bash_tool = ExecuteBashTool(config.docker, docker_runtime)
        tool_executor.register_tool(execute_bash_tool)
        # checkBashTask shares this agent's task table: no cross-agent visibility.
        tool_executor.register_tool(CheckBashTaskTool(execute_bash_tool.background_tasks))
        allowed_paths = config.tool.allowed_paths
        tool_executor.register_tool(ReadFileTool(config.docker, docker_runtime, allowed_paths))
        tool_executor.register_tool(WriteFileTool(config.docker, docker_runtime, allowed_paths))
        tool_executor.register_tool(EditFileTool(config.docker, docker_runtime, allowed_paths))
        tool_executor.register_tool(SendEmailTool())
        tool_executor.register_tool(ListPrototypesTool(lister=list_prototypes))
        # Spawn/stop lifecycle tools (stage 6b): the registry is the
        # assembler, so it injects itself (it satisfies the AgentLifecycle
        # port structurally); spawner_id binds the tool to the owning
        # agent for budget accounting and audit.
        tool_executor.register_tool(SpawnAgentTool(registry=self, spawner_id=config.id))
        tool_executor.register_tool(StopAgentTool(registry=self, spawner_id=config.id))
        tool_executor.register_tool(ListAgentsTool(registry=self))
        # Spec authoring (stage 4a): writes untrusted prototypes to the
        # staging area; author_id is recorded as the :author for audit.
        tool_executor.register_tool(
            WritePrototypeTool(author_id=config.id, writer=write_staging_prototype)
        )
        tool_executor.register_tool(RememberFactTool(memory))
        tool_executor.register_tool(SearchMemoriesTool(memory))
        tool_executor.register_tool(CompactTool(memory))
        tool_executor.register_tool(ForgetMemoryTool(memory))
        tool_executor.register_tool(CreateTaskTool(task_manager))
        tool_executor.register_tool(ListTasksTool(task_manager))
        tool_executor.register_tool(StartTaskTool(task_manager))
        tool_executor.register_tool(PauseTaskTool(task_manager))
        tool_executor.register_tool(CompleteTaskTool(task_manager))
        tool_executor.register_tool(RemoveTaskTool(task_manager))
        tool_executor.register_tool(GetCurrentTaskTool(task_manager))
        logger.debug(
            "tools_registered",
            agent_name=config.name,
            tool_count=len(tool_executor.list_tools()),
        )

        # Initialize tools
        await tool_executor.initialize()

        # Prepare team members config (all other agents)
        all_ids = self._agent_info.get_all_ids()
        team_members = [
            cfg
            for aid in all_ids
            if aid != config.id
            and (cfg := self._agent_info.get_config(aid)) is not None
        ]

        # Read tool timeout from agent config
        per_tool_timeout = config.tool.per_timeout

        return CognitiveCapabilities(
            config=config,
            llm_client=llm_client,
            memory=memory,
            task_manager=task_manager,
            tool_executor=tool_executor,
            team_members=team_members,
            per_tool_timeout=per_tool_timeout,
            registry=self,
        )

    # ==================== Lifecycle request execution ====================

    async def _on_mail_delivered(self, event: Event) -> None:
        """Wake-on-mail (phase E): issue a wake for a sleeping subscriber.

        Mechanized policy — the spec is the single lifecycle authority:
        a MAIL_DELIVERED whose recipient is registered, not running/waking,
        and whose spec declares ``:on :mail-delivered`` gets an
        AGENT_START_REQUEST{reason="wake"} that joins the delivery's trace
        tree (``_fulfill_wake`` continues the same trace). The mail is
        already durably in the member's inbox, so no trigger event is
        carried — the woken member scans unread mail on boot
        (docs/design/mail.md). Previously this was Adam's wake rule; the
        decision now lives here so lifecycle policy is spec-derived.
        """
        recipient = event.payload.get("recipient")
        if not isinstance(recipient, str):
            return
        if self._agents.get(recipient) is not None or recipient in self._waking:
            return  # already running or waking — no request needed
        if self._agent_info.get_config(recipient) is None:
            return  # unknown member
        spec = self._spec_profile(recipient)
        if spec is None or not any(
            rule.on is EventType.MAIL_DELIVERED for rule in spec.rules
        ):
            return  # spec does not subscribe to mail delivery
        await self.event_bus.publish(
            Event(
                type=EventType.AGENT_START_REQUEST,
                payload=StartRequest(agent_id=recipient, reason="wake"),
                source="agent_registry",
                trace_id=event.trace_id,
                span_id=event.span_id,
                parent_span_id=event.parent_span_id,
            )
        )

    async def _on_start_request(self, event: Event) -> None:
        """Execute an AGENT_START_REQUEST decision (mechanism only).

        Runs in the EventBus dispatch context, so it must stay fast: the
        actual assembly happens in a background task. Idempotent — a
        running or already-waking agent, or an unknown id, is skipped.
        The dedup state is updated synchronously here so back-to-back
        requests for the same persona collapse to one assembly.
        """
        request = event.payload
        if not isinstance(request, StartRequest):
            return

        agent_id = request.agent_id
        if agent_id in self._agents or agent_id in self._waking:
            return
        if not self._agent_info.get_config(agent_id):
            return
        self._waking.add(agent_id)
        asyncio.create_task(self._fulfill_start_request(request, event))

    async def _fulfill_start_request(self, request: StartRequest, event: Event) -> None:
        """Assemble one agent under its wake lock, per the request's reason.

        Serialized per agent by a lock: concurrent requests for the same
        persona trigger exactly one assembly.

        reason="wake": publishes AGENT_WAKING / AGENT_WOKEN /
        AGENT_WAKE_FAILED joining the delivering event's trace tree. There
        is no redelivery: the mail is already in the agent's inbox and the
        freshly started agent scans its unread mail on boot — the mailbox
        is the queue (docs/design/mail.md).

        reason="boot": starts the agent with log lines only (no
        observability events) — same as boot always did.
        """
        agent_id = request.agent_id
        lock = self._wake_locks.setdefault(agent_id, asyncio.Lock())
        try:
            async with lock:
                if request.reason == "wake":
                    await self._fulfill_wake(agent_id, event)
                else:
                    await self._fulfill_boot(agent_id, request.reason)
        finally:
            self._waking.discard(agent_id)

    async def _start_agent_guarded(self, agent_id: str) -> MemberRuntime | None:
        """``_start_agent`` with a timeout: a hung assembly must not wedge
        the per-agent wake lock forever. Returns None on timeout (same
        contract as ``_start_agent`` failure)."""
        try:
            return await asyncio.wait_for(
                self._start_agent(agent_id), timeout=self._START_TIMEOUT_SECONDS
            )
        except TimeoutError:
            logger.error(
                "agent_start_timeout",
                agent_id=agent_id,
                timeout_seconds=self._START_TIMEOUT_SECONDS,
            )
            return None

    async def _fulfill_boot(self, agent_id: str, reason: str) -> None:
        """Start an agent without wake observability (boot path)."""
        started_at = time.monotonic()
        logger.info("agent_starting", agent_id=agent_id, reason=reason)
        agent = await self._start_agent_guarded(agent_id)
        if agent is None:
            logger.error("agent_start_failed", agent_id=agent_id, reason=reason)
            return
        logger.info(
            "agent_started",
            agent_id=agent_id,
            reason=reason,
            start_duration_seconds=round(time.monotonic() - started_at, 3),
        )

    async def _fulfill_wake(self, agent_id: str, request_event: Event) -> None:
        """Wake a sleeping agent; its boot scan picks up the waiting mail."""
        started_at = time.monotonic()
        logger.info("agent_waking", agent_id=agent_id)
        await self.event_bus.publish(
            Event(
                type=EventType.AGENT_WAKING,
                payload={"agent_id": agent_id},
                source="agent_registry",
                trace_id=request_event.trace_id,
                span_id=request_event.span_id,
                parent_span_id=request_event.parent_span_id,
            )
        )

        # Join the mail's trace tree so wake cost is visible as an
        # "agent.wake" span in the same trace.
        if request_event.trace_id:
            with (
                continue_trace(request_event.trace_id, request_event.span_id),
                start_span(
                    "agent.wake",
                    source="agent_registry",
                    attributes={"agent_id": agent_id},
                ),
            ):
                agent = await self._start_agent_guarded(agent_id)
        else:
            with start_span(
                "agent.wake",
                source="agent_registry",
                attributes={"agent_id": agent_id},
            ):
                agent = await self._start_agent_guarded(agent_id)

        wake_duration = time.monotonic() - started_at

        if agent is None:
            logger.error("agent_wake_failed", agent_id=agent_id)
            await self.event_bus.publish(
                Event(
                    type=EventType.AGENT_WAKE_FAILED,
                    payload={"agent_id": agent_id},
                    source="agent_registry",
                    trace_id=request_event.trace_id,
                    span_id=request_event.span_id,
                    parent_span_id=request_event.parent_span_id,
                )
            )
            return

        logger.info(
            "agent_woken",
            agent_id=agent_id,
            wake_duration_seconds=round(wake_duration, 3),
        )
        await self.event_bus.publish(
            Event(
                type=EventType.AGENT_WOKEN,
                payload={
                    "agent_id": agent_id,
                    "wake_duration_seconds": round(wake_duration, 3),
                },
                source="agent_registry",
                trace_id=request_event.trace_id,
                span_id=request_event.span_id,
                parent_span_id=request_event.parent_span_id,
            )
        )

    async def _on_stop_request(self, event: Event) -> None:
        """Execute an AGENT_STOP_REQUEST decision (mechanism only).

        Runs in the EventBus dispatch context; the drain itself (agent
        shutdown) happens in a background task.
        """
        request = event.payload
        if not isinstance(request, StopRequest):
            return
        asyncio.create_task(self._drain_agent(request.agent_id, request.reason))

    # ==================== Hot adoption (out-of-process creation) ====================

    async def adopt_agent(self, agent_id: str) -> str:
        """Adopt an agent created out-of-process (wizard/mirror), no restart.

        External tools write ``agent.yaml`` directly; this makes the running
        server pick the new agent up: the provider reloads the config, the
        agent is registered, and non-lazy agents are started immediately
        (same assembly path as boot/spawn). A lazy agent is registered only
        and wakes on its first mail, same as at boot.

        Args:
            agent_id: id of the agent to adopt

        Returns:
            One of "already-known" (running or registered — no-op; a known
            agent's config is never reloaded here), "config-not-found" (no
            agent.yaml on disk), "registered-lazy", "started", "start-failed"
            (``agent_start_failed`` already logged by ``_start_agent``).
        """
        if agent_id in self._agents or agent_id in self._registered_ids:
            result = "already-known"
        elif not await self._agent_info.reload_agent(agent_id):
            result = "config-not-found"
        else:
            self.register(agent_id)
            self._profiles.pop(agent_id, None)  # spec may have changed (phase E)
            config = self._agent_info.get_config(agent_id)
            if config is not None and self.is_lazy(agent_id):
                result = "registered-lazy"
            else:
                agent = await self._start_agent(agent_id)
                result = "started" if agent is not None else "start-failed"

        logger.info("agent_adopted", agent_id=agent_id, result=result)
        return result

    # ==================== Spawn / stop (stage 6b) ====================

    async def spawn_contractor(
        self,
        prototype: str,
        name: str,
        spawner_id: str,
        session_id: str | None,
        params: dict[str, Any] | None = None,
    ) -> AgentConfig:
        """Spawn a contractor from a prototype and start it immediately.

        Pipeline: budget checks -> id generation -> ``instantiate()`` (writes
        persona + behavior + agent.yaml to disk) -> contractor fields
        (role/depth/spawned_by; laziness is role-derived, phase E) ->
        ``reload_agent`` so the config is visible to the provider ->
        ``register`` + ``_start_agent`` -> ``AGENT_SPAWNED`` event.

        Permissions cannot escalate: the only source is the prototype
        library (trusted, Tlon-prebuilt or user-installed), and the
        behavior file is copied verbatim — a spec is total, so there is
        nothing to override and a spawner cannot grant capabilities beyond
        the prototype.

        Orphan policy (reparent, not kill): the contractor is registered
        independently of its spawner; when the spawner sleeps or stops, the
        contractor keeps running and is reclaimed by its own idle timeout.

        Args:
            prototype: prototype directory name under ~/.tlon/prototypes/
            name: display name of the new contractor
            spawner_id: id of the agent requesting the spawn (audit + depth)
            session_id: session the spawn belongs to (quota bucket); None
                buckets all context-less spawns of the spawner together

        Returns:
            The new contractor's AgentConfig (role="contractor").

        Raises:
            SpawnRejectedError: a budget cap was hit (see ``.reason``)
            PrototypeError: prototype missing
        """
        tlon_paths = self._paths or paths

        # Trust gate (stage 4a): before any budget check, an untrusted
        # prototype (LLM-authored, awaiting human approval) cannot spawn.
        # A missing prototype falls through to instantiate's not-found error.
        info = get_prototype_info(prototype, tlon_paths)
        if info is not None and not info.trusted:
            self._reject_spawn(
                "untrusted_prototype",
                spawner_id=spawner_id,
                session_id=session_id,
                prototype=prototype,
                detail=f"prototype '{prototype}' is untrusted (staged, awaiting approval)",
            )

        # Budget 1: global activation cap (residents included)
        if len(self._agents) >= self._max_activations:
            self._reject_spawn(
                "max_activations",
                spawner_id=spawner_id,
                session_id=session_id,
                prototype=prototype,
                detail=f"running agents {len(self._agents)} >= cap {self._max_activations}",
            )

        # Budget 2: spawn-chain depth (residents are depth 0)
        spawner_config = self._agent_info.get_config(spawner_id)
        spawner_depth = spawner_config.spawn_depth if spawner_config else 0
        depth = spawner_depth + 1
        if depth > self._max_spawn_depth:
            self._reject_spawn(
                "max_spawn_depth",
                spawner_id=spawner_id,
                session_id=session_id,
                prototype=prototype,
                detail=f"depth {depth} > cap {self._max_spawn_depth}",
            )

        # Budget 3: per-(spawner, session) quota
        quota_key = (spawner_id, session_id or _UNKNOWN_SESSION)
        used = self._spawn_quota.get(quota_key, 0)
        if used >= self._session_spawn_quota:
            self._reject_spawn(
                "session_quota",
                spawner_id=spawner_id,
                session_id=session_id,
                prototype=prototype,
                detail=f"quota {self._session_spawn_quota} per session exhausted",
            )

        # Id is generated here (never caller-supplied): sanitize the
        # prototype fragment so the id stays within the AgentConfig charset
        safe_prototype = "".join(c for c in prototype if c.isalnum() or c in "-_")
        agent_id = f"c-{safe_prototype}-{uuid.uuid4().hex[:8]}"

        # Writes persona + behavior + agent.yaml (copy semantics; behavior
        # frozen at creation time). PrototypeError propagates to the caller.
        config = instantiate(
            prototype, agent_id, name, params=params, tlon_paths=tlon_paths
        )

        # Contractor identity: disposable, always lazy (role-derived in
        # phase E — no activation field), short idle fuse. Re-save so the
        # YAML on disk carries the final config.
        config.role = "contractor"
        config.spawned_by = spawner_id
        config.spawn_depth = depth
        config.idle_timeout_seconds = self._contractor_idle_timeout_seconds
        yaml_save(config, tlon_paths.tlon_home())

        # Make the config visible to the provider (router, status queries)
        if not await self._agent_info.reload_agent(agent_id):
            self._reject_spawn(
                "config_reload_failed",
                spawner_id=spawner_id,
                session_id=session_id,
                prototype=prototype,
                detail=f"provider could not load new config for '{agent_id}'",
            )

        self._spawn_quota[quota_key] = used + 1
        self.register(agent_id)
        agent = await self._start_agent(agent_id)

        logger.info(
            "agent_spawned",
            agent_id=agent_id,
            agent_name=name,
            prototype=prototype,
            spawner_id=spawner_id,
            spawn_depth=depth,
            started=agent is not None,
        )
        await self.event_bus.publish(
            Event(
                type=EventType.AGENT_SPAWNED,
                payload={
                    "agent_id": agent_id,
                    "name": name,
                    "prototype": prototype,
                    "spawner_id": spawner_id,
                    "spawn_depth": depth,
                    "started": agent is not None,
                },
                source="agent_registry",
            )
        )
        return config

    def _reject_spawn(
        self,
        reason: str,
        *,
        spawner_id: str,
        session_id: str | None,
        prototype: str,
        detail: str,
    ) -> NoReturn:
        """Log and raise a budget rejection (single funnel for audit)."""
        logger.warning(
            "spawn_rejected",
            reason=reason,
            spawner_id=spawner_id,
            session_id=session_id,
            prototype=prototype,
            detail=detail,
        )
        raise SpawnRejectedError(reason, f"spawn rejected ({reason}): {detail}")

    async def stop_contractor(self, agent_id: str, stopped_by: str) -> bool:
        """Stop a contractor: drain-check, shutdown, delete its home dir.

        Drain semantics mirror the idle reaper: a contractor with any live
        (ACTIVE/WAITING) session is never stopped. Residents are never
        stoppable through this path — their home directories live forever.

        Args:
            agent_id: contractor to stop
            stopped_by: agent id requesting the stop (audit)

        Returns:
            True when the contractor was stopped and discarded.

        Raises:
            StopRejectedError: unknown agent / resident / live sessions
        """
        config = self._agent_info.get_config(agent_id)
        if config is None:
            raise StopRejectedError("unknown_agent", f"agent '{agent_id}' not found")
        if config.role != "contractor":
            raise StopRejectedError(
                "resident_not_stoppable",
                f"agent '{agent_id}' is a resident; only contractors can be stopped",
            )

        agent = self._agents.get(agent_id)
        if agent is not None and agent.active_session_count > 0:
            raise StopRejectedError(
                "active_sessions",
                f"agent '{agent_id}' has {agent.active_session_count} live session(s); "
                "wait for them to finish (drain)",
            )

        # Serialize with a concurrent wake of the same persona
        lock = self._wake_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            agent = self._agents.pop(agent_id, None)
            if agent is not None:
                # Drain re-check under the lock: a session that started in
                # the race window keeps the contractor alive
                if agent.active_session_count > 0:
                    self._agents[agent_id] = agent
                    raise StopRejectedError(
                        "active_sessions",
                        f"agent '{agent_id}' gained a live session; stop aborted",
                    )
                try:
                    await agent.shutdown()
                except Exception as e:
                    # Already out of the run table; discard proceeds so the
                    # disposable persona does not leak
                    logger.error("agent_stop_failed", agent_id=agent_id, error=str(e))

            await self._discard_contractor(agent_id, stopped_by=stopped_by)

        logger.info("contractor_stopped", agent_id=agent_id, stopped_by=stopped_by)
        return True

    async def _discard_contractor(self, agent_id: str, stopped_by: str) -> None:
        """Remove a stopped contractor: container, registration, home dir.

        Only ever called for role=="contractor" agents. The sandbox
        container is force-removed and the home directory
        (``~/.tlon/{id}/``) is deleted — contractors are disposable; a
        resident's container and directory are never touched by the
        registry.
        """
        if agent_id in self._registered_ids:
            self._registered_ids.remove(agent_id)
        self._profiles.pop(agent_id, None)  # phase E: spec profile cache

        # Reclaim the sandbox container before the home dir goes: the
        # container bind-mounts the workspace under it (). Runs after
        # MemberRuntime.shutdown() (which drains tool execution, ) at
        # both call sites. A missing runtime (daemon was unavailable at
        # assembly, or the member never got cognitive capabilities) or a
        # removal failure only logs — the discard must not be blocked.
        runtime = self._docker_runtimes.pop(agent_id, None)
        if runtime is not None:
            try:
                await runtime.remove(force=True)
            except Exception as e:
                logger.warning(
                    "container_reclaim_failed", agent_id=agent_id, error=str(e)
                )

        self._assert_deletable_agent_id(agent_id)
        try:
            self._agent_info.delete_agent(agent_id)
        except ValueError:
            # Home directory already gone — cache entry removed regardless
            logger.warning("contractor_home_missing", agent_id=agent_id)
        logger.info("contractor_home_deleted", agent_id=agent_id, stopped_by=stopped_by)

        await self.event_bus.publish(
            Event(
                type=EventType.AGENT_STOPPED,
                payload={
                    "agent_id": agent_id,
                    "stopped_by": stopped_by,
                    "role": "contractor",
                },
                source="agent_registry",
            )
        )

    def _assert_deletable_agent_id(self, agent_id: str) -> None:
        """Path-injection guard for home-directory deletion.

        Spawned ids are generated as ``c-{prototype}-{8hex}`` and carry no
        separators, but the guard is enforced anyway: the id must satisfy
        the AgentConfig charset and resolve strictly inside tlon_home.
        """
        if not agent_id.replace("-", "").replace("_", "").isalnum():
            raise StopRejectedError(
                "invalid_agent_id", f"agent id '{agent_id}' fails the charset check"
            )
        home = (self._paths or paths).tlon_home().resolve()
        target = (home / agent_id).resolve()
        if target.parent != home:
            raise StopRejectedError(
                "invalid_agent_id",
                f"agent id '{agent_id}' resolves outside tlon_home",
            )

    # ==================== Idle fact detection + drain execution ====================

    async def _idle_monitor_loop(self) -> None:
        """Periodically report idle lazy agents (scale-to-zero facts).

        Every `_IDLE_CHECK_INTERVAL_SECONDS`, each running lazy agent that
        has been idle past its configured timeout and has no live sessions
        is reported as an AGENT_IDLE fact event. Stopping it is a policy
        decision (Adam's); the registry only executes the resulting
        AGENT_STOP_REQUEST.
        """
        while True:
            await asyncio.sleep(self._IDLE_CHECK_INTERVAL_SECONDS)
            for agent_id in list(self._agents.keys()):
                try:
                    await self._report_if_idle(agent_id)
                except Exception as e:
                    logger.error("agent_idle_check_failed", agent_id=agent_id, error=str(e))

    async def _report_if_idle(self, agent_id: str) -> None:
        """Publish an AGENT_IDLE fact if the agent is idle past its timeout.

        Fact detection only — no state is changed here. An agent with any
        live (ACTIVE/WAITING) session is never reported idle.
        """
        config = self._agent_info.get_config(agent_id)
        if not config or not self.is_lazy(agent_id):
            return

        agent = self._agents.get(agent_id)
        if agent is None:
            return
        if agent.active_session_count > 0 or agent.idle_seconds < config.idle_timeout_seconds:
            return

        idle = int(agent.idle_seconds)
        logger.info("agent_idle_detected", agent_id=agent_id, idle_seconds=idle)
        await self.event_bus.publish(
            Event(
                type=EventType.AGENT_IDLE,
                payload={"agent_id": agent_id, "idle_seconds": idle},
                source="agent_registry",
            )
        )

    async def _drain_agent(self, agent_id: str, reason: str) -> None:
        """Execute a stop request: drain one idle lazy agent.

        The fact that motivated the request may be stale, so the condition
        is re-checked under the per-agent lock right before shutdown — a
        session that started (or activity that resumed) in the race window
        keeps the agent alive.
        """
        config = self._agent_info.get_config(agent_id)
        if not config or not self.is_lazy(agent_id):
            return

        agent = self._agents.get(agent_id)
        if agent is None:
            return
        if agent.active_session_count > 0 or agent.idle_seconds < config.idle_timeout_seconds:
            return

        # Serialize with a concurrent start of the same persona
        lock = self._wake_locks.setdefault(agent_id, asyncio.Lock())
        async with lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                return
            # Drain re-check under the lock
            if agent.active_session_count > 0:
                return
            if agent.idle_seconds < config.idle_timeout_seconds:
                return

            self._agents.pop(agent_id)
            idle = int(agent.idle_seconds)
            try:
                await agent.shutdown()
            except Exception as e:
                # The agent is already out of the run table; a later
                # start request assembles a fresh activation.
                logger.error("agent_sleep_failed", agent_id=agent_id, error=str(e))
                return

            # Contractors are disposable: draining discards the persona
            # entirely (home dir deleted) instead of putting it to sleep.
            if config.role == "contractor":
                logger.info(
                    "contractor_idle_reaped",
                    agent_id=agent_id,
                    idle_seconds=idle,
                    reason=reason,
                )
                await self._discard_contractor(agent_id, stopped_by="idle_drain")
                return

            logger.info("agent_sleeping", agent_id=agent_id, idle_seconds=idle, reason=reason)
            await self.event_bus.publish(
                Event(
                    type=EventType.AGENT_SLEEPING,
                    payload={"agent_id": agent_id, "idle_seconds": idle},
                    source="agent_registry",
                )
            )

    def get_agent(self, agent_id: str) -> MemberRuntime | None:
        """Get an active agent by ID.

        Args:
            agent_id: Agent ID

        Returns:
            The MemberRuntime instance if active, None otherwise
        """
        return self._agents.get(agent_id)

    def get_all_agents(self) -> list[MemberRuntime]:
        """Get all currently active agents.

        Returns:
            List of active MemberRuntime instances
        """
        return list(self._agents.values())

    def get_all_ids(self) -> list[str]:
        """Get all registered agent IDs.

        Returns:
            List of all agent IDs from AgentInfoProvider
        """
        return list(self._agent_info.get_all_ids())

    def get_all(self) -> list[str]:
        """Get all registered agent IDs.

        Alias for get_all_ids() for backward compatibility.

        Returns:
            List of all agent IDs
        """
        return self.get_all_ids()

    def get_config(self, agent_id: str) -> AgentConfig | None:
        """Get an agent's configuration.

        Delegates to AgentInfoProvider.

        Args:
            agent_id: Agent ID

        Returns:
            AgentConfig or None if not found
        """
        return self._agent_info.get_config(agent_id)

    def get_agent_status(self, agent_id: str) -> dict[str, Any] | None:
        """Get status of a specific agent.

        Args:
            agent_id: Agent ID

        Returns:
            Agent status dictionary or None if not found
        """
        config = self._agent_info.get_config(agent_id)
        if not config:
            return None

        agent = self._agents.get(agent_id)

        if agent:
            state = agent.get_state()
        elif agent_id in self._waking:
            state = "waking"
        elif self.is_lazy(agent_id):
            state = "sleeping"
        else:
            state = "unknown"

        return {
            "id": agent_id,
            "name": config.name,
            "state": state,
            "role": config.role,
            "prototype": config.prototype,
            "spawned_by": config.spawned_by,
            "spawn_depth": config.spawn_depth,
        }

    def get_all_agent_status(self) -> list[dict[str, Any]]:
        """Get status of all registered agents.

        Returns:
            List of agent status dictionaries
        """
        return [
            status
            for agent_id in self._agent_info.get_all_ids()
            if (status := self.get_agent_status(agent_id)) is not None
        ]
