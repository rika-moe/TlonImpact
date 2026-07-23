"""Agent Registry for managing multiple agents.

This module provides AgentRegistry class that manages:
- Agent lifecycle (activation-aware: "always" agents start at boot,
  "lazy" agents register without starting)
- Wake-on-mention (lazy agents are activated when explicitly @mentioned)
- Idle drain (lazy agents sleep after idle_timeout_seconds of inactivity;
  idle contractors are discarded entirely, home directory included)
- Spawn/stop of contractors (stage 6b): prototype instantiation under a
  spawn budget (global activation cap, spawn depth, per-session quota)
- Agent shutdown

Configuration is delegated to AgentInfoProvider (typically ConfigManager).

Architecture:
    AgentRegistry is a Component that depends on:
    - EventBus (for agent event publishing and MESSAGE_ROUTED subscription)
    - AgentInfoProvider (for configuration queries)
    - MessageStore (for agent history access)

    All dependencies are injected via constructor (DAG rule).
"""

import asyncio
import time
import uuid
from contextlib import suppress
from typing import TYPE_CHECKING, Any, NoReturn

from tlon.agents.agent import Agent
from tlon.agents.prototypes import instantiate
from tlon.agents.spawn_errors import SpawnRejectedError, StopRejectedError
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.component import Component
from tlon.base.config import AgentConfig, LLMConfig, _yaml_save
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths, paths
from tlon.base.tracing.trace_context import continue_trace, start_span
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.organization.messaging.store import MessageStore
from tlon.sandbox.docker_runtime import DockerRuntime
from tlon.tools.agents.list_agents import ListAgentsTool
from tlon.tools.agents.spawn_agent import SpawnAgentTool
from tlon.tools.agents.stop_agent import StopAgentTool
from tlon.tools.filesystem.edit_file import EditFileTool
from tlon.tools.filesystem.read_file import ReadFileTool
from tlon.tools.filesystem.write_file import WriteFileTool
from tlon.tools.memory.compact import CompactTool
from tlon.tools.memory.forget_memory import ForgetMemoryTool
from tlon.tools.memory.remember_fact import RememberFactTool
from tlon.tools.memory.search_memories import SearchMemoriesTool
from tlon.tools.messaging.send_message import SendMessageTool
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


def _build_llm_config(file_config: LLMConfig | None, settings) -> LLMConfig:
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


class AgentRegistry(Component):
    """Registry for managing multiple agents.

    Activation-aware lifecycle (see docs/design/dsl-agents.md, 阶段 6a):
    - "always" agents start at initialize() and never sleep (status quo);
    - "lazy" agents register but do not start at boot. They wake when an
      explicit @mention routes to them (wake-on-mention) and are drained
      back to sleep after `idle_timeout_seconds` of inactivity with no
      live sessions. All-lazy configuration boots with 0 running agents.

    Configuration is provided by AgentInfoProvider (typically ConfigManager),
    allowing this class to focus on agent lifecycle management only.

    Dependencies (constructor injected):
        - event_bus: EventBus for agent event publishing
        - agent_info_provider: AgentInfoProvider for configuration queries
        - message_store: MessageStore (optional)

    Example:
        >>> registry = AgentRegistry(
        ...     event_bus=event_bus,
        ...     agent_info_provider=config_manager,
        ...     message_store=message_store,
        ... )
        >>> registry.register("dev-001")  # Register by ID
        >>> await registry.initialize()  # Start "always" agents
        >>> agent = registry.get_agent("dev-001")  # Get running agent
    """

    # Idle monitor check period (seconds)
    _IDLE_CHECK_INTERVAL_SECONDS = 30

    def __init__(
        self,
        event_bus: EventBus,
        agent_info_provider: "AgentInfoProvider",
        message_store: MessageStore | None = None,
        paths: "TlonPaths | None" = None,
        max_activations: int = 8,
        max_spawn_depth: int = 3,
        session_spawn_quota: int = 2,
        contractor_idle_timeout_seconds: int = 300,
    ):
        """Initialize the agent registry.

        All dependencies are injected via constructor (DAG architecture).

        Args:
            event_bus: Event bus for message routing
            agent_info_provider: Provider for agent configuration queries
            message_store: Message store for agent history access
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
        self.message_store = message_store
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

        # Running agents (activations)
        self._agents: dict[str, Agent] = {}

        # Wake-on-mention state: agents currently being woken, and one
        # lock per agent serializing wake/drain of the same persona
        self._waking: set[str] = set()
        self._wake_locks: dict[str, asyncio.Lock] = {}

        # MESSAGE_ROUTED subscription + idle monitor task
        self._routed_subscription: str | None = None
        self._idle_monitor_task: asyncio.Task | None = None

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

    async def initialize(self) -> None:
        """Initialize the registry and start registered agents in parallel.

        Only agents with ``activation == "always"`` are started here;
        ``activation == "lazy"`` agents stay registered but sleeping until
        an explicit @mention wakes them. Each agent gets up to 60 seconds
        to start. Agents that fail or time out are logged and skipped —
        the server continues operating with the agents that did start.

        Also subscribes to MESSAGE_ROUTED (wake-on-mention) and starts
        the idle monitor (drain of idle lazy agents).
        """
        logger.info("agent_registry_initializing", agent_ids=self._registered_ids)

        # Wake-on-mention subscription (idempotent across re-initialize)
        if self._routed_subscription is None:
            self._routed_subscription = self.event_bus.subscribe(
                EventType.MESSAGE_ROUTED, self._on_message_routed
            )

        # Idle drain monitor (idempotent across re-initialize)
        if self._idle_monitor_task is None:
            self._idle_monitor_task = asyncio.create_task(self._idle_monitor_loop())

        start_ids: list[str] = []
        lazy_ids: list[str] = []
        for aid in self._registered_ids:
            if aid in self._agents:
                continue
            config = self._agent_info.get_config(aid)
            if config and config.activation == "lazy":
                lazy_ids.append(aid)
                logger.info("agent_lazy_not_started", agent_id=aid)
            else:
                start_ids.append(aid)

        if not start_ids:
            self._initialized = True
            logger.info(
                "agent_registry_initialized",
                active_count=len(self._agents),
                sleeping_agents=lazy_ids,
            )
            return

        async def _start_one(agent_id: str) -> tuple[str, Exception | None]:
            try:
                logger.info("agent_starting", agent_id=agent_id)
                await self._start_agent(agent_id)
                logger.info("agent_started", agent_id=agent_id)
                return agent_id, None
            except Exception as e:
                logger.error("agent_start_failed", agent_id=agent_id, error=str(e))
                return agent_id, e

        per_agent_timeout = 60
        overall_timeout = len(start_ids) * per_agent_timeout

        tasks = [asyncio.create_task(_start_one(aid)) for aid in start_ids]
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=False),
                timeout=overall_timeout,
            )
        except TimeoutError:
            logger.error(
                "agent_startup_timeout",
                timeout_seconds=overall_timeout,
            )
            for t in tasks:
                if not t.done():
                    t.cancel()
            for t in tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await t

        self._initialized = True
        active_count = len(self._agents)
        total = len(start_ids)
        logger.info(
            "agent_registry_initialized",
            active_count=active_count,
            total=total,
            active_agents=list(self._agents.keys()),
            sleeping_agents=lazy_ids,
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

        # Stop wake-on-mention subscription
        if self._routed_subscription is not None:
            self.event_bus.unsubscribe(self._routed_subscription)
            self._routed_subscription = None

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
        self._initialized = False

        logger.info("agent_registry_shutdown_completed")

    async def _start_agent(self, agent_id: str) -> Agent | None:
        """Assemble all dependencies and start an agent.

        This is the sole assembler for Agent and its infrastructure.
        All deps are constructed here and injected via Agent's constructor.

        Args:
            agent_id: ID of the agent to start

        Returns:
            The started Agent instance, or None if not found
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

            from tlon.base.settings import get_settings

            # Build infrastructure dependencies
            llm_config = _build_llm_config(config.llm, get_settings())
            llm_client = LLMClient(llm_config)

            agent_home = (self._paths or paths).agent_dir(config.id)
            memory_config = (
                config.memory if isinstance(config.memory, MemoryConfig) else MemoryConfig()
            )
            memory_config.agent_id = config.id
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
            # The container is NOT started here: DockerRuntime lazily calls
            # ensure_running() on first execute/read/write, so waking an
            # agent for pure conversation costs zero containers. A daemon
            # failure therefore surfaces as a tool error, not a start failure.
            docker_runtime: DockerRuntime | None = None
            try:
                docker_runtime = DockerRuntime(config.docker, config.id)
            except Exception as e:
                logger.warning("docker_unavailable", agent_name=config.name, error=str(e))

            # Create ToolExecutor (direct execution, no EventBus)
            tool_executor = ToolExecutor()

            # Register tools
            tool_executor.register_tool(ExecuteBashTool(config.docker, docker_runtime))
            tool_executor.register_tool(CheckBashTaskTool())
            allowed_paths = config.tool.allowed_paths
            tool_executor.register_tool(ReadFileTool(config.docker, docker_runtime, allowed_paths))
            tool_executor.register_tool(WriteFileTool(config.docker, docker_runtime, allowed_paths))
            tool_executor.register_tool(EditFileTool(config.docker, docker_runtime, allowed_paths))
            tool_executor.register_tool(SendMessageTool())
            tool_executor.register_tool(ListPrototypesTool())
            # Spawn/stop lifecycle tools (stage 6b): the registry is the
            # assembler, so it injects itself; spawner_id binds the tool
            # to the owning agent for budget accounting and audit.
            tool_executor.register_tool(SpawnAgentTool(registry=self, spawner_id=config.id))
            tool_executor.register_tool(StopAgentTool(registry=self, spawner_id=config.id))
            tool_executor.register_tool(ListAgentsTool(registry=self))
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
                self._agent_info.get_config(aid)
                for aid in all_ids
                if aid != agent_id and self._agent_info.get_config(aid)
            ]

            # Read tool timeout from agent config
            per_tool_timeout = config.tool.per_timeout

            # Create and initialize agent with all deps injected
            agent = Agent(
                config=config,
                event_bus=self.event_bus,
                llm_client=llm_client,
                memory=memory,
                task_manager=task_manager,
                tool_executor=tool_executor,
                team_members=team_members,
                message_store=self.message_store,
                per_tool_timeout=per_tool_timeout,
            )
            await agent.initialize()

            # Track active agent
            self._agents[agent_id] = agent

            # Publish agent started event
            event = Event(
                type=EventType.AGENT_STARTED,
                payload={"agent_id": agent_id, "name": config.name},
                source="agent_registry",
            )
            await self.event_bus.publish(event)

            logger.info("agent_started", agent_id=agent_id, agent_name=config.name)
            return agent

        except Exception as e:
            logger.error("agent_start_failed", agent_id=agent_id, error=str(e), exc_info=True)
            return None

    # ==================== Wake-on-mention ====================

    async def _on_message_routed(self, event: Event) -> None:
        """Wake sleeping agents targeted by explicit @mentions.

        Runs in the EventBus dispatch context, so it must stay fast: the
        actual assembly happens in a background task. Only mention-resolved
        targets wake an agent — proactive broadcast targets never do,
        otherwise scale-to-zero would be defeated by every general message.
        """
        from tlon.organization.messaging.router import RoutedMessage

        if not isinstance(event.payload, RoutedMessage):
            return

        for agent_id in event.payload.mention_target_ids:
            if agent_id in self._agents or agent_id in self._waking:
                continue
            if not self._agent_info.get_config(agent_id):
                continue
            self._waking.add(agent_id)
            asyncio.create_task(self._wake_and_redeliver(agent_id, event))

    async def _wake_and_redeliver(self, agent_id: str, event: Event) -> None:
        """Activate a sleeping agent and redeliver the event that woke it.

        Serialized per agent by a lock: concurrent mentions of the same
        persona trigger exactly one assembly. After ``_start_agent`` the
        agent is in the run table, so re-publishing the original
        MESSAGE_ROUTED event (same payload and trace carrier) is picked up
        by the new agent's own handler and cannot trigger another wake —
        that is the loop guard.
        """
        lock = self._wake_locks.setdefault(agent_id, asyncio.Lock())
        try:
            async with lock:
                started_at = time.monotonic()
                logger.info("agent_waking", agent_id=agent_id)
                await self.event_bus.publish(
                    Event(
                        type=EventType.AGENT_WAKING,
                        payload={"agent_id": agent_id},
                        source="agent_registry",
                        trace_id=event.trace_id,
                        span_id=event.span_id,
                        parent_span_id=event.parent_span_id,
                    )
                )

                # Join the routed message's trace tree so wake cost is
                # visible as an "agent.wake" span in the same trace.
                if event.trace_id:
                    with (
                        continue_trace(event.trace_id, event.span_id),
                        start_span(
                            "agent.wake",
                            source="agent_registry",
                            attributes={"agent_id": agent_id},
                        ),
                    ):
                        agent = await self._start_agent(agent_id)
                else:
                    with start_span(
                        "agent.wake",
                        source="agent_registry",
                        attributes={"agent_id": agent_id},
                    ):
                        agent = await self._start_agent(agent_id)

                wake_duration = time.monotonic() - started_at

                if agent is None:
                    logger.error("agent_wake_failed", agent_id=agent_id)
                    await self.event_bus.publish(
                        Event(
                            type=EventType.AGENT_WAKE_FAILED,
                            payload={"agent_id": agent_id},
                            source="agent_registry",
                            trace_id=event.trace_id,
                            span_id=event.span_id,
                            parent_span_id=event.parent_span_id,
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
                        trace_id=event.trace_id,
                        span_id=event.span_id,
                        parent_span_id=event.parent_span_id,
                    )
                )

                # Redeliver the original event: same RoutedMessage payload
                # and trace carrier, so the new agent's handler picks it up.
                await self.event_bus.publish(event)
        finally:
            self._waking.discard(agent_id)

    # ==================== Spawn / stop (stage 6b) ====================

    async def spawn_contractor(
        self,
        prototype: str,
        name: str,
        spawner_id: str,
        session_id: str | None,
        overrides: dict[Any, Any] | None = None,
    ) -> AgentConfig:
        """Spawn a contractor from a prototype and start it immediately.

        Pipeline: budget checks -> id generation -> ``instantiate()`` (writes
        persona + behavior + agent.yaml to disk) -> contractor fields
        (role/depth/spawned_by/lazy activation) -> ``reload_agent`` so the
        config is visible to the provider -> ``register`` + ``_start_agent``
        -> ``AGENT_SPAWNED`` event.

        Permissions cannot escalate: the only source is the prototype
        library (trusted, Tlon-prebuilt or user-installed), and ``overrides``
        merge into behavior fields only — tools are not part of the spec
        schema, so a spawner cannot grant capabilities beyond the prototype.

        Orphan policy (reparent, not kill): the contractor is registered
        independently of its spawner; when the spawner sleeps or stops, the
        contractor keeps running and is reclaimed by its own idle timeout.

        Args:
            prototype: prototype directory name under ~/.tlon/prototypes/
            name: display name of the new contractor
            spawner_id: id of the agent requesting the spawn (audit + depth)
            session_id: session the spawn belongs to (quota bucket); None
                buckets all context-less spawns of the spawner together
            overrides: behavior-only diff deep-merged over the prototype

        Returns:
            The new contractor's AgentConfig (role="contractor").

        Raises:
            SpawnRejectedError: a budget cap was hit (see ``.reason``)
            PrototypeError: prototype missing
        """
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

        tlon_paths = self._paths or paths
        # Id is generated here (never caller-supplied): sanitize the
        # prototype fragment so the id stays within the AgentConfig charset
        safe_prototype = "".join(c for c in prototype if c.isalnum() or c in "-_")
        agent_id = f"c-{safe_prototype}-{uuid.uuid4().hex[:8]}"

        # Writes persona + behavior + agent.yaml (copy semantics; behavior
        # frozen at creation time). PrototypeError propagates to the caller.
        config = instantiate(
            prototype, agent_id, name, overrides=overrides, tlon_paths=tlon_paths
        )

        # Contractor identity: disposable, lazy-activated, short idle fuse.
        # Re-save so the YAML on disk carries the final config.
        config.role = "contractor"
        config.spawned_by = spawner_id
        config.spawn_depth = depth
        config.activation = "lazy"
        config.idle_timeout_seconds = self._contractor_idle_timeout_seconds
        _yaml_save(config, tlon_paths.tlon_home())

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
        """Remove a stopped contractor: registration, config cache, home dir.

        Only ever called for role=="contractor" agents. The home directory
        (``~/.tlon/{id}/``) is deleted — contractors are disposable; a
        resident's directory is never touched by the registry.
        """
        if agent_id in self._registered_ids:
            self._registered_ids.remove(agent_id)

        self._assert_deletable_agent_id(agent_id)
        try:
            self._agent_info.delete_agent(agent_id, delete_data=True)
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

    # ==================== Idle drain ====================

    async def _idle_monitor_loop(self) -> None:
        """Periodically drain idle lazy agents (scale-to-zero).

        Every `_IDLE_CHECK_INTERVAL_SECONDS`, each running lazy agent that
        has been idle past its configured timeout and has no live sessions
        is shut down and removed from the run table (its registration is
        kept, so a later @mention wakes it again).
        """
        while True:
            await asyncio.sleep(self._IDLE_CHECK_INTERVAL_SECONDS)
            for agent_id in list(self._agents.keys()):
                try:
                    await self._reap_if_idle(agent_id)
                except Exception as e:
                    logger.error("agent_sleep_check_failed", agent_id=agent_id, error=str(e))

    async def _reap_if_idle(self, agent_id: str) -> None:
        """Put one lazy agent to sleep if it is idle past its timeout.

        Drain semantics: an agent with any live (ACTIVE/WAITING) session is
        never stopped. The condition is checked once up front and again
        under the per-agent lock right before shutdown — a session that
        started in the race window keeps the agent alive.
        """
        config = self._agent_info.get_config(agent_id)
        if not config or config.activation != "lazy":
            return

        agent = self._agents.get(agent_id)
        if agent is None:
            return
        if agent.active_session_count > 0 or agent.idle_seconds < config.idle_timeout_seconds:
            return

        # Serialize with a concurrent wake of the same persona
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
                # @mention assembles a fresh activation.
                logger.error("agent_sleep_failed", agent_id=agent_id, error=str(e))
                return

            # Contractors are disposable: idle reaping discards the persona
            # entirely (home dir deleted) instead of putting it to sleep.
            if config.role == "contractor":
                logger.info("contractor_idle_reaped", agent_id=agent_id, idle_seconds=idle)
                await self._discard_contractor(agent_id, stopped_by="idle_drain")
                return

            logger.info("agent_sleeping", agent_id=agent_id, idle_seconds=idle)
            await self.event_bus.publish(
                Event(
                    type=EventType.AGENT_SLEEPING,
                    payload={"agent_id": agent_id, "idle_seconds": idle},
                    source="agent_registry",
                )
            )

    def get_agent(self, agent_id: str) -> Agent | None:
        """Get an active agent by ID.

        Args:
            agent_id: Agent ID

        Returns:
            The Agent instance if active, None otherwise
        """
        return self._agents.get(agent_id)

    def get_all_agents(self) -> list[Agent]:
        """Get all currently active agents.

        Returns:
            List of active Agent instances
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

    def unregister(self, agent_id: str) -> bool:
        """Unregister and stop an agent.

        Args:
            agent_id: Agent ID to unregister

        Returns:
            True if agent was found and removed
        """
        # Check if registered or active
        is_registered = agent_id in self._registered_ids
        is_active = agent_id in self._agents

        if not is_registered and not is_active:
            return False

        # Stop if active
        if is_active:
            agent = self._agents.pop(agent_id)
            asyncio.create_task(agent.shutdown())

        # Remove from registered list
        if is_registered:
            self._registered_ids.remove(agent_id)

        logger.info("agent_unregistered", agent_id=agent_id)
        return True

    def update_config(self, agent_id: str, config: AgentConfig) -> bool:
        """Update an agent's configuration.

        Note: Configuration updates should be done through AgentInfoProvider
        (e.g., ConfigManager). This method only validates that the agent
        is registered and logs a warning if the agent is active.

        Args:
            agent_id: Agent ID to update
            config: New configuration (for validation only)

        Returns:
            True if agent is registered
        """
        if agent_id not in self._registered_ids and agent_id not in self._agents:
            return False

        # If agent is active, we should restart it
        # For now, just log the warning
        if agent_id in self._agents:
            logger.warning("agent_restart_required", agent_id=agent_id)

        logger.info("agent_config_updated", agent_id=agent_id)
        return True

    def get_agent_status(self, agent_id: str) -> dict | None:
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
        elif config.activation == "lazy":
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

    def get_all_agent_status(self) -> list[dict]:
        """Get status of all registered agents.

        Returns:
            List of agent status dictionaries
        """
        return [self.get_agent_status(agent_id) for agent_id in self._agent_info.get_all_ids()]
