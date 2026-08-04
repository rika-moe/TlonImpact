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

import asyncio
import contextlib
import json
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from structlog.contextvars import bound_contextvars

from tlon.agents.context_builder import ContextBuilder
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.on_complete import OnCompleteRunner
from tlon.agents.prompt_context import AgentPromptContext
from tlon.agents.reflection import ReflectionEngine
from tlon.agents.scheduler import SessionScheduler
from tlon.agents.session import AgentSession
from tlon.agents.spec import AgentSpec
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.trigger import TriggerEvaluator
from tlon.base.component import Component
from tlon.base.config import AgentConfig
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMClient, LLMResponse, ToolCall
from tlon.base.logger import get_logger
from tlon.base.tracing.trace_context import continue_trace, get_current_trace, start_span
from tlon.memory.manager import MemoryManager
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.messaging.member import Member
from tlon.tools.base import ToolResult
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

        # Tool execution timeout
        self._per_tool_timeout = per_tool_timeout

        # Prompt cache — rebuilt when persona file mtimes change
        self._system_prompt_cache: str | None = None
        self._system_prompt_mtime: tuple[tuple[str, float | None], ...] | None = None

        # Behavior spec: the single semantic authority the subsystems read
        # (see docs/design/dsl-agents.md). Loaded before subsystem construction.
        self.spec = spec if spec is not None else AgentSpec.load(self)

        # Behavioral subsystems (constructed with a back-reference to self)
        self._trigger = TriggerEvaluator(self)
        self._reflection = ReflectionEngine(self)
        self._context_builder = ContextBuilder(self)
        self._loop_driver = LoopDriver(self)
        self._on_complete = OnCompleteRunner(self)
        self._scheduler = SessionScheduler(self)

        # Agent state
        self.state: AgentState | None = None

        # Mail queue for serial processing; items are (Mail, trace carrier).
        # Bounded: a full queue drops new mail rather than blocking the
        # EventBus dispatch (see _on_mail_delivered). The mailbox itself is
        # the durable queue — a dropped item stays unread and is re-enqueued
        # on the next boot scan.
        self._mail_queue: asyncio.Queue[tuple[Mail, dict[str, str | None]]] = (
            asyncio.Queue(maxsize=config.message_queue_size)
        )
        self._worker_task: asyncio.Task | None = None
        self._worker_running = False

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

    def _derive_state(self, sessions: dict[str, AgentSession]) -> None:
        """Recompute agent-level state from live sessions.

        With multi-session scheduling, per-step assignment would let one
        session's lifecycle overwrite another's. Instead the state is
        derived: THINKING while any session is still alive, ACTIVE (idle)
        when none remain. Lifecycle states (INITIALIZING/ERROR/SHUTDOWN)
        are left untouched.
        """
        if self.state not in (AgentState.ACTIVE, AgentState.THINKING):
            return
        if any(not s.is_completed for s in sessions.values()):
            self.state = AgentState.THINKING
        else:
            self.state = AgentState.ACTIVE

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

            self._worker_running = True
            self._worker_task = asyncio.create_task(self._main_loop())

            self.state = AgentState.ACTIVE
            self._initialized = True
            logger.info("agent_ready", agent_name=self.name)

    async def shutdown(self) -> None:
        """Shutdown the agent."""
        with bound_contextvars(agent_id=self.id):
            logger.info("agent_shutdown", agent_name=self.name)

            self._worker_running = False
            if self._worker_task:
                self._worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._worker_task

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

        try:
            self._mail_queue.put_nowait((mail, carrier))
        except asyncio.QueueFull:
            logger.warning(
                "mail_dropped",
                sender=mail.from_address,
                queue_size=self._mail_queue.maxsize,
            )
            return
        logger.debug("mail_queued", sender=mail.from_address, mail_id=mail.id)

    # ── Subsystem delegates (thin seams kept for tests and orchestration) ──

    async def _main_loop(self) -> None:
        """Run the session scheduling loop (see SessionScheduler)."""
        await self._scheduler.run()

    def _should_respond(self, mail: Mail) -> bool:
        """Determine if we should respond to this mail."""
        return self._trigger.should_respond(mail)

    def _is_addressed(self, mail: Mail) -> bool:
        """Check if this agent's address is in the mail's To or CC."""
        return self._trigger.is_addressed(mail)

    async def _step_session(self, session: AgentSession, mail: Mail) -> None:
        """Advance a session by one ReAct step (see LoopDriver)."""
        await self._loop_driver.step_session(session, mail)

    async def _collect_session_results(self, session: AgentSession) -> None:
        """Collect completed background tools for a WAITING session."""
        await self._loop_driver.collect_session_results(session)

    async def _execute_one_tool(self, tc: ToolCall) -> tuple[ToolCall, ToolResult]:
        """Execute one tool with timeout (see LoopDriver)."""
        return await self._loop_driver.execute_one_tool(tc)

    async def _process_message(self, mail: Mail) -> None:
        """Process a single mail synchronously (used by tests)."""
        await self._loop_driver.process_message(mail)

    async def _mark_mail_read(self, mail: Mail | None) -> None:
        """Advance the inbox cursor once a mail's session has completed.

        Called by the scheduler on every session completion (success,
        error or timeout alike — the mail has been processed). Never
        raises: a cursor failure must not break session cleanup.
        """
        if mail is None or self.mailbox is None:
            return
        try:
            await self.mailbox.mark_read(mail.id)
        except Exception as e:
            self._logger.error("mail_mark_read_failed", mail_id=mail.id, error=str(e))

    async def _build_knowledge_context(self) -> str:
        """Build knowledge text from memory blocks."""
        return await self._context_builder.build_knowledge_context()

    async def _build_context(self, current_mail: Mail) -> list[dict[str, Any]]:
        """Build conversation context for LLM."""
        return await self._context_builder.build_context(current_mail)

    async def _build_prompt_context(self) -> AgentPromptContext:
        """Build AgentPromptContext for the system prompt."""
        return await self._context_builder.build_prompt_context()

    def _persona_files(self) -> list[Path]:
        """Persona files that feed the system prompt."""
        return self._context_builder.persona_files()

    @staticmethod
    def _persona_mtime_signature(files: list[Path]) -> tuple[tuple[str, float | None], ...]:
        """Snapshot persona file mtimes; missing files map to None."""
        return ContextBuilder.persona_mtime_signature(files)

    async def _get_system_prompt(self) -> str:
        """Get system prompt, rebuilding the cache when persona files change."""
        return await self._context_builder.get_system_prompt()

    async def _check_reflection_trigger(self) -> None:
        """Check if memory reflection should run and trigger if needed."""
        await self._reflection.check_trigger()

    async def _run_on_complete_actions(self, session: AgentSession, result: str) -> None:
        """Evaluate + execute on-complete action expressions (see OnCompleteRunner)."""
        await self._on_complete.run(session, result)

    async def think(self, context: list[dict[str, Any]]) -> LLMResponse:
        """Call LLM to think and generate response."""
        system_prompt = await self._get_system_prompt()
        tools = self.tool_executor.get_tool_definitions()

        current_trace = get_current_trace()

        await self.emit(
            EventType.AGENT_THINKING_START,
            payload={"agent_id": self.id, "agent_name": self.name, "context_length": len(context)},
            source=self.id,
            trace_id=current_trace.trace_id if current_trace else None,
            span_id=current_trace.span_id if current_trace else None,
            parent_span_id=current_trace.parent_span_id if current_trace else None,
        )

        await self.emit(
            EventType.AGENT_LLM_REQUEST,
            payload={
                "agent_id": self.id,
                "model": self.llm_client.config.model_name
                if hasattr(self.llm_client, "config")
                else "unknown",
                "messages_count": len(context),
                "tools_count": len(tools) if tools else 0,
            },
            source=self.id,
            trace_id=current_trace.trace_id if current_trace else None,
            span_id=current_trace.span_id if current_trace else None,
        )

        import time

        start_time = time.time()
        response = await self.llm_client.chat(
            system_prompt=system_prompt,
            messages=context,
            tools=tools if tools else None,
        )
        duration_ms = int((time.time() - start_time) * 1000)

        await self.emit(
            EventType.AGENT_LLM_RESPONSE,
            payload={
                "agent_id": self.id,
                "model": self.llm_client.config.model_name
                if hasattr(self.llm_client, "config")
                else "unknown",
                "duration_ms": duration_ms,
                "finish_reason": response.finish_reason,
                "has_tool_calls": bool(response.tool_calls),
                "tool_call_count": len(response.tool_calls) if response.tool_calls else 0,
            },
            source=self.id,
            trace_id=current_trace.trace_id if current_trace else None,
            span_id=current_trace.span_id if current_trace else None,
        )

        await self.emit(
            EventType.AGENT_THINKING_END,
            payload={"agent_id": self.id, "agent_name": self.name, "duration_ms": duration_ms},
            source=self.id,
            trace_id=current_trace.trace_id if current_trace else None,
            span_id=current_trace.span_id if current_trace else None,
            parent_span_id=current_trace.parent_span_id if current_trace else None,
        )

        return response

    async def _save_interaction_to_memory(
        self, final_response: str, llm_response: LLMResponse, tool_results: list
    ) -> None:
        """Save interaction to memory (gated by spec.on_complete.save_interaction)."""
        if not self.spec.on_complete.save_interaction:
            return
        try:
            msg = {
                "sender_id": self.id,
                "sender_name": self.name,
                "sender_type": "agent",
                "content": final_response,
            }
            await self.memory.add_interaction(msg, llm_response, tool_results)
            self._reflection.note_interaction()
        except Exception as e:
            self._logger.error("memory_save_failed", error=str(e), exc_info=True)

    async def _send_mail(
        self,
        to: list[str],
        content: str,
        cc: list[str] | None = None,
        subject: str | None = None,
        in_reply_to: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        """Send a mail via EventBus, injecting the current trace context.

        Reply headers (``in_reply_to`` / ``thread_id``) are passed
        explicitly by the caller, which derives them from the session's
        trigger mail; both default to a fresh thread.
        """
        try:
            mail = Mail.create(
                from_address=self.address,
                to=to,
                cc=cc,
                subject=subject,
                content=content,
                in_reply_to=in_reply_to,
                thread_id=thread_id,
            )
            with start_span(
                "mail.send",
                source=self.id,
                attributes={"mail_id": mail.id, "to": mail.to},
            ):
                current = get_current_trace()
                await self.emit(
                    EventType.MAIL_NEW,
                    payload=mail,
                    source=self.id,
                    trace_id=current.trace_id if current else None,
                    span_id=current.span_id if current else None,
                    parent_span_id=current.parent_span_id if current else None,
                )
        except Exception:
            self._logger.error("mail_emit_failed", exc_info=True)

    @contextlib.contextmanager
    def _session_span(self, session: AgentSession):
        """Per-round trace context for a session's advance.

        OTel context is contextvar-based, so it does not survive across
        event-loop turns: each round re-attaches the carrier stored on the
        session and opens an "agent.session" span around that round's work
        (think step or tool-result collection).
        """
        carrier = session.carrier or {}
        trace_id = carrier.get("trace_id")
        with contextlib.ExitStack() as stack:
            # session_id is also bound so tools executing within this round
            # (background tasks inherit contextvars) can attribute their
            # work to the session — e.g. spawnAgent's per-session quota.
            stack.enter_context(bound_contextvars(agent_id=self.id, session_id=session.id))
            if trace_id:
                stack.enter_context(continue_trace(trace_id, carrier.get("span_id")))
            stack.enter_context(
                start_span(
                    "agent.session",
                    source=self.id,
                    attributes={
                        "session_id": session.id,
                        "state": session.state.value,
                        "spec_hash": self.spec.source_hash,
                    },
                )
            )
            yield

    @contextlib.asynccontextmanager
    async def _traced_span(self, name: str):
        """Thin wrapper over the tracing facade.

        Persistence is handled by the OTel JSONL exporter; this only
        manages the span lifecycle and yields the span context.
        """
        with start_span(name=name, source=self.id) as ctx:
            yield ctx

    @staticmethod
    def _tool_call_to_dict(tool_call: ToolCall) -> dict[str, Any]:
        """Convert ToolCall to dict for context."""
        return {
            "id": tool_call.id,
            "type": "function",
            "function": {
                "name": tool_call.function_name,
                "arguments": json.dumps(tool_call.arguments),
            },
        }

    @staticmethod
    def _tool_result_to_string(result: ToolResult) -> str:
        """Convert tool result to string for context."""
        if hasattr(result, "to_dict"):
            return json.dumps(result.to_dict())
        return str(result)
