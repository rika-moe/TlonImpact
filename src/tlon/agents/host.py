"""Host protocols for Agent subsystems (P4 — subsystem decoupling).

Each behavioral subsystem declares the narrow slice of the Agent it
actually collaborates with, instead of holding a back-reference to the
whole Agent:

- Subsystems that only need injected services or plain data receive them
  directly (TriggerEvaluator, ReflectionEngine, ContextBuilder) and have
  no host protocol at all.
- Subsystems that drive the agent's shared orchestration surface declare
  one Protocol each (interface segregation — one consumer, one protocol):
  :class:`LoopDriverHost`, :class:`OnCompleteHost`, :class:`SchedulerHost`.

``Agent`` satisfies all three protocols structurally: no inheritance, no
registration, and it never appears in a subsystem's constructor signature.

These protocols are explicit contract documentation. The project's mypy
configuration exists but is not enforced in CI (and does not currently
pass on the wider codebase), so the protocols are kept in sync with the
Agent's delegate surface by review — when an Agent delegate used by a
subsystem changes, update the corresponding protocol member here.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, AbstractContextManager
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import structlog

    from tlon.agents.session import AgentSession
    from tlon.agents.spec import AgentSpec
    from tlon.base.config import AgentConfig
    from tlon.base.events.event import EventType
    from tlon.base.llm_client import LLMResponse, ToolCall
    from tlon.memory.manager import MemoryManager
    from tlon.organization.mail.mail import Mail
    from tlon.tools.base import ToolResult


class LoopDriverHost(Protocol):
    """The orchestration surface the LoopDriver needs from its host.

    Everything here is a genuine cross-subsystem concern owned by the
    Agent: the LLM call, event/trace plumbing, mail sending, memory
    writes, agent-state derivation, and the on-complete handoff.
    """

    _logger: structlog.stdlib.BoundLogger
    """Per-agent bound logger (agent_id already bound as a field)."""

    id: str
    """Agent id — event sources, log fields, session id prefixes."""

    config: AgentConfig
    """Agent configuration; the driver reads ``config.name`` for DSL envs."""

    spec: AgentSpec
    """Behavior spec — the loop program (stages/until/limits) is read here."""

    async def think(self, context: list[dict[str, Any]]) -> LLMResponse:
        """Call the LLM with the round's conversation context."""
        ...

    async def emit(
        self,
        event_type: EventType,
        payload: Any,
        *,
        source: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> None:
        """Publish an observability event on the bus (see Device.emit)."""
        ...

    def _traced_span(self, name: str) -> AbstractAsyncContextManager[Any]:
        """Open a named trace span and yield its span context."""
        ...

    def _session_span(self, session: AgentSession) -> AbstractContextManager[None]:
        """Per-round trace context for a session's advance."""
        ...

    async def _send_mail(
        self,
        to: list[str],
        content: str,
        cc: list[str] | None = None,
        subject: str | None = None,
        in_reply_to: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        """Send a mail via the EventBus, injecting the current trace context."""
        ...

    def _derive_state(self, sessions: dict[str, AgentSession]) -> None:
        """Recompute agent-level state from the live session set."""
        ...

    async def _save_interaction_to_memory(
        self, final_response: str, llm_response: LLMResponse, tool_results: list
    ) -> None:
        """Persist a completed interaction (gated by the spec)."""
        ...

    async def _run_on_complete_actions(self, session: AgentSession, result: str) -> None:
        """Evaluate and execute the spec's on-complete action expressions."""
        ...

    async def _mark_mail_read(self, mail: Mail | None) -> None:
        """Advance the inbox cursor once a mail's session has completed."""
        ...

    async def _build_context(self, current_mail: Mail) -> list[dict[str, Any]]:
        """Build the LLM conversation context for a trigger mail."""
        ...

    def _tool_call_to_dict(self, tool_call: ToolCall) -> dict[str, Any]:
        """Serialize a tool call for inclusion in the conversation context."""
        ...

    def _tool_result_to_string(self, result: ToolResult) -> str:
        """Serialize a tool result for inclusion in the conversation context."""
        ...


class OnCompleteHost(Protocol):
    """The surface the OnCompleteRunner needs from its host.

    Identity (for the DSL ``self`` env and spawn attribution), the
    behavior spec (action forms), logging, and mail sending for ``emit``
    actions. The spawn registry is injected separately — it is a service,
    not part of the host surface.
    """

    _logger: structlog.stdlib.BoundLogger
    """Per-agent bound logger (agent_id already bound as a field)."""

    id: str
    """Agent id — DSL ``self`` env and spawn ``spawner_id`` attribution."""

    @property
    def name(self) -> str:
        """Agent display name — DSL ``self`` env (read-only on Component)."""
        ...

    spec: AgentSpec
    """Behavior spec — ``on_complete.actions`` holds the action forms."""

    async def _send_mail(
        self,
        to: list[str],
        content: str,
        cc: list[str] | None = None,
        subject: str | None = None,
        in_reply_to: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        """Send a mail via the EventBus (executes ``emit`` actions)."""
        ...


class SchedulerHost(Protocol):
    """The surface the SessionScheduler needs from its host.

    Session advancement itself is delegated to the injected LoopDriver,
    not routed through this protocol. The mail queue and worker lifecycle
    are owned by the scheduler (R2) — the protocol declares contracts,
    not shared mutable state.
    """

    id: str
    """Agent id — session id prefixes and the loop's log binding."""

    config: AgentConfig
    """Agent configuration; the scheduler reads the session timeout."""

    memory: MemoryManager
    """Memory manager — the idle-timeout check runs once per loop pass."""

    def _session_span(self, session: AgentSession) -> AbstractContextManager[None]:
        """Per-round trace context for a session's advance."""
        ...

    async def _mark_mail_read(self, mail: Mail | None) -> None:
        """Advance the inbox cursor once a mail's session has completed."""
        ...

    def _derive_state(self, sessions: dict[str, AgentSession]) -> None:
        """Recompute agent-level state from the live session set."""
        ...

    async def _check_reflection_trigger(self) -> None:
        """Run the memory-reflection trigger check (proactive, no mail)."""
        ...


__all__ = [
    "LoopDriverHost",
    "OnCompleteHost",
    "SchedulerHost",
]
