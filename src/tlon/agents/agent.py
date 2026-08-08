"""Agent implementation for Tlon.

The core intelligent entity that can receive messages, think,
execute tools, and respond.

Agent is a thin orchestration shell: it owns the Component/Device
lifecycle, the inbound message queue, the LLM call (think) and the
trace/memory plumbing, and delegates behavior to subsystems:

- SessionScheduler (scheduler.py): main loop + session lifecycle
- LoopDriver (loop_driver.py): ReAct step/collect
- TriggerEvaluator (trigger.py): should-respond decisions
- ContextBuilder (context_builder.py): conversation/system-prompt assembly
- ReflectionEngine (reflection.py): background memory audit
- OnCompleteRunner (on_complete.py): on-complete action expressions (4b)

Behavior parameters (trigger rules, context sections, loop limits,
on-complete actions) come from ``self.spec`` (AgentSpec, spec.py) —
the agent is an interpreter of that spec, per docs/design/dsl-agents.md.
"""

import contextlib
from collections.abc import Iterator
from enum import Enum
from typing import TYPE_CHECKING, Any

from structlog.contextvars import bound_contextvars

from tlon.agents.context_builder import ContextBuilder
from tlon.agents.executor import EffectExecutor
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.on_complete import OnCompleteRunner
from tlon.agents.reflection import ReflectionEngine
from tlon.agents.scheduler import SessionScheduler
from tlon.agents.session import AgentSession
from tlon.agents.spec import AgentSpec
from tlon.agents.stages.services import StageServices, make_event_publisher
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.trigger import AgentIdentity, TriggerEvaluator
from tlon.base.component import Component
from tlon.base.config import AgentConfig
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.memory.manager import MemoryManager
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.messaging.member import Member
from tlon.tools.tasks.manager import TaskManager

if TYPE_CHECKING:
    from tlon.agents.agent_registry import AgentRegistry

logger = get_logger(__name__)


class AgentState(Enum):
    """Agent lifecycle states."""

    INITIALIZING = "initializing"  # Starting up
    ACTIVE = "active"  # Running and processing messages
    THINKING = "thinking"  # Currently processing a message
    ERROR = "error"  # Encountered an error
    SHUTDOWN = "shutdown"  # Stopped


class Agent(Member):
    """An intelligent agent that can process messages and execute tools.

    Member: a Congress bus citizen (see docs/design/members.md).
    Device: holds an EventBus reference and publishes observability events.
    All infrastructure dependencies are injected via constructor.

    ReAct loop is implemented via step/collect scheduling in the main loop
    (SessionScheduler) with multi-session support. Tool execution uses
    background asyncio tasks (LoopDriver).

    Example:
        >>> config = AgentConfig(id="dev-001", name="林知遥", ...)
        >>> agent = Agent(config, event_bus, llm_client, memory, ...)
        >>> await agent.initialize()
        >>> # Agent will now respond to messages via EventBus
    """

    def __init__(
        self,
        config: AgentConfig,
        event_bus: EventBus,
        llm_client: LLMClient,
        memory: MemoryManager,
        task_manager: TaskManager,
        tool_executor: ToolExecutor,
        team_members: list["AgentConfig"] | None = None,
        mailbox: Mailbox | None = None,
        per_tool_timeout: float = 30.0,
        spec: AgentSpec | None = None,
        registry: "AgentRegistry | None" = None,
    ):
        """Initialize the agent.

        Args:
            config: Agent configuration
            event_bus: Event bus for publishing/subscribing
            llm_client: LLM client for inference
            memory: Memory manager for long-term storage
            task_manager: Task manager for task tracking
            tool_executor: Tool registry and executor
            team_members: List of all team member configs
            mailbox: The agent's own mailbox (inbox/sent/unread cursor);
                injected by the registry, None only in bare test setups
            per_tool_timeout: Timeout for each individual tool
            spec: Behavior spec; when None, loaded from the agent's
                behavior.tlon file (missing/invalid file raises — the
                agent refuses to boot)
            registry: Agent registry for on-complete ``spawn`` actions;
                None (tests, standalone construction) disables them
        """
        Component.__init__(self, name=config.name)
        Device.__init__(self, event_bus=event_bus, name=config.name)

        self.config = config
        self.team_members = team_members or []
        self.mailbox = mailbox

        self.id = config.id

        # Injected infrastructure dependencies
        self.llm_client = llm_client
        self.memory = memory
        self.task_manager = task_manager
        self.tool_executor = tool_executor
        self.registry = registry

        # Per-agent bound logger (agent_id as a field, logger name stays __name__)
        self._logger = logger.bind(agent_id=config.id)

        # Behavior spec: the single semantic authority the subsystems read
        # (see docs/design/dsl-agents.md). Loaded before subsystem construction.
        self.spec = spec if spec is not None else AgentSpec.load(config.id)

        # Behavioral subsystems (P4/C2): assembled from capabilities — the
        # injected services above, plain data, and closures over the event
        # bus — never a reference to self.
        identity = AgentIdentity(id=self.id, name=self.name, address=self.address)
        publisher = make_event_publisher(event_bus, self.name)
        self._trigger = TriggerEvaluator(identity, self.spec)
        self._reflection = ReflectionEngine(memory, llm_client, self.spec)
        self._context_builder = ContextBuilder(
            config, self.team_members, mailbox, memory, self.spec, self.address
        )
        # Stage services (C1) and effect executor (C2): the query/
        # observability bundle the pure stages use and the engine-side
        # executor of stage-declared effects — both assembled from the
        # injected dependencies above, no back-reference to self.
        self._stage_services = StageServices(
            identity=identity,
            spec=self.spec,
            llm_client=llm_client,
            context_builder=self._context_builder,
            publish=publisher,
            tool_definitions=tool_executor.get_tool_definitions,
            logger=self._logger,
        )
        self._effect_executor = EffectExecutor(
            identity=identity,
            spec=self.spec,
            memory=memory,
            reflection=self._reflection,
            mailbox=mailbox,
            publish=publisher,
            logger=self._logger,
        )
        self._on_complete = OnCompleteRunner(
            identity, self.spec, self._logger, self._effect_executor, registry
        )
        self._loop_driver = LoopDriver(
            identity=identity,
            spec=self.spec,
            services=self._stage_services,
            executor=self._effect_executor,
            tool_executor=tool_executor,
            per_tool_timeout=per_tool_timeout,
            on_complete=self._on_complete,
        )
        self._scheduler = SessionScheduler(
            config,
            memory,
            self._stage_services.session_span,
            self._effect_executor.mark_mail_read,
            self._reflection.check_trigger,
            self._loop_driver,
        )

        # Agent state (backing field; see the state property)
        self._state: AgentState | None = None

        with bound_contextvars(agent_id=self.id):
            logger.info("agent_initialized", agent_name=self.name)

    @property
    def member_id(self) -> str:
        """Member identity on the bus (same as the agent ID)."""
        return self.id

    @property
    def member_type(self) -> str:
        """Member type; agents are ReAct members."""
        return "react"

    @property
    def state(self) -> "AgentState | None":
        """Agent state; ACTIVE/THINKING derive from live sessions on read.

        State derivation is read-time (R2 follow-up): the engine never
        calls back into the Agent to write it — ACTIVE vs THINKING is
        computed from the scheduler's live session set whenever the state
        is read. Lifecycle states (INITIALIZING/ERROR/SHUTDOWN) are stored
        verbatim and never derived.
        """
        if self._state in (AgentState.ACTIVE, AgentState.THINKING):
            if self._scheduler.active_session_count > 0:
                return AgentState.THINKING
            return AgentState.ACTIVE
        return self._state

    @state.setter
    def state(self, value: "AgentState | None") -> None:
        self._state = value

    def get_state(self) -> str:
        """Get agent state as string."""
        return self.state.value if self.state else "unknown"

    @property
    def active_session_count(self) -> int:
        """Number of live sessions (ACTIVE or WAITING). Read-only; used by
        the registry's idle monitor for drain decisions."""
        return self._scheduler.active_session_count

    @property
    def idle_seconds(self) -> float:
        """Seconds since the last session activity.

        "Activity" covers session creation, per-step updates of a live
        session's ``last_active_at``, and session completion; an agent
        that never had a session counts from main-loop start. Read-only;
        used by the registry's idle monitor.
        """
        return self._scheduler.idle_seconds

    async def initialize(self) -> None:
        """Initialize the agent."""
        with bound_contextvars(agent_id=self.id):
            self.state = AgentState.INITIALIZING
            logger.info("agent_initializing", agent_name=self.name)

            await self.memory.load()
            self.task_manager.load()

            self.subscribe(EventType.MAIL_DELIVERED, self._on_mail_delivered)

            # The mailbox is the durable queue (docs/design/mail.md): mail
            # that arrived while the agent slept sits unread in the inbox.
            # Scan it once at boot and enqueue each unread mail — this is
            # the whole replacement for the old redelivery pipeline.
            if self.mailbox is not None:
                try:
                    for mail in await self.mailbox.unread():
                        self._enqueue_mail(mail, {"trace_id": None, "span_id": None})
                except Exception as e:
                    self._logger.error("mail_unread_scan_failed", error=str(e), exc_info=True)

            self._scheduler.start()

            self.state = AgentState.ACTIVE
            self._initialized = True
            logger.info("agent_ready", agent_name=self.name)

    async def shutdown(self) -> None:
        """Shutdown the agent."""
        with bound_contextvars(agent_id=self.id):
            logger.info("agent_shutdown", agent_name=self.name)

            await self._scheduler.stop()

            self.unsubscribe_all()

            self.state = AgentState.SHUTDOWN
            self._initialized = False
            logger.info("agent_shutdown_complete", agent_name=self.name)

    async def _on_mail_delivered(self, event: Event) -> None:
        """Handle mail delivered to this agent's inbox.

        The delivery component publishes one MAIL_DELIVERED per recipient;
        only the copy addressed to this agent is considered. Runs in the
        EventBus dispatch context shared by all agents, so the agent_id
        binding must be scoped (bound_contextvars resets on exit).
        """
        with bound_contextvars(agent_id=self.id):
            payload = event.payload
            if not isinstance(payload, dict) or not isinstance(payload.get("mail"), Mail):
                return
            if payload.get("recipient") != self.id:
                return
            mail: Mail = payload["mail"]
            carrier = {"trace_id": event.trace_id, "span_id": event.span_id}
            self._enqueue_mail(mail, carrier)

    def _enqueue_mail(self, mail: Mail, carrier: dict[str, str | None]) -> None:
        """Run the trigger gate and enqueue a mail for session processing.

        Backpressure: when the bounded queue is full, the mail is dropped
        with a warning instead of blocking the EventBus dispatch. It stays
        unread in the mailbox and is picked up again by the boot scan.
        """
        if mail.from_address == self.address:
            return

        if not self._should_respond(mail):
            return

        if not self._scheduler.enqueue(mail, carrier):
            logger.warning(
                "mail_dropped",
                sender=mail.from_address,
                queue_size=self._scheduler._mail_queue.maxsize,
            )
            return
        logger.debug("mail_queued", sender=mail.from_address, mail_id=mail.id)

    # ── Subsystem delegates (thin seams kept for tests and orchestration) ──

    def _should_respond(self, mail: Mail) -> bool:
        """Determine if we should respond to this mail."""
        return self._trigger.should_respond(mail)

    async def _process_message(self, mail: Mail) -> None:
        """Process a single mail synchronously (used by tests)."""
        await self._loop_driver.process_message(mail)

    async def _mark_mail_read(self, mail: Mail | None) -> None:
        """Advance the inbox cursor once a mail's session has completed.

        Called by the scheduler on every session completion (success,
        error or timeout alike — the mail has been processed). The
        implementation lives in the EffectExecutor; this delegate
        remains for the SessionScheduler's host surface.
        """
        await self._effect_executor.mark_mail_read(mail)

    async def _build_knowledge_context(self) -> str:
        """Build knowledge text from memory blocks."""
        return await self._context_builder.build_knowledge_context()

    async def _build_context(self, current_mail: Mail) -> list[dict[str, Any]]:
        """Build conversation context for LLM."""
        return await self._context_builder.build_context(current_mail)

    async def _get_system_prompt(self) -> str:
        """Get system prompt, rebuilding the cache when persona files change."""
        return await self._context_builder.get_system_prompt()

    async def _check_reflection_trigger(self) -> None:
        """Check if memory reflection should run and trigger if needed."""
        await self._reflection.check_trigger()

    @contextlib.contextmanager
    def _session_span(self, session: AgentSession) -> Iterator[None]:
        """Per-round trace context for a session's advance.

        The implementation lives in StageServices.session_span; this
        delegate remains for the SessionScheduler's host surface.
        """
        with self._stage_services.session_span(session):
            yield
