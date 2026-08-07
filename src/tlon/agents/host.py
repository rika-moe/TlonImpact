"""Host protocol for the SessionScheduler (P4 — subsystem decoupling).

Behavioral subsystems receive capabilities, not a back-reference to the
whole Agent:

- Subsystems that only need injected services or plain data receive them
  directly (TriggerEvaluator, ReflectionEngine, ContextBuilder,
  OnCompleteRunner) and have no host protocol at all.
- The LoopDriver runs on injected capabilities alone (StageServices /
  EffectExecutor / plain data + closures) since C2 (2026-08): with every
  stage purified to ``Session x Services -> StageOutcome``, its host
  protocol was deleted.
- Only the SessionScheduler still declares a host protocol
  (:class:`SchedulerHost`) — agent state derives on read (the
  ``Agent.state`` property), so no state-derivation callback is part of
  it.

This protocol is explicit contract documentation. The project's mypy
configuration exists but is not enforced in CI (and does not currently
pass on the wider codebase), so the protocol is kept in sync with the
Agent's delegate surface by review.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:

    from tlon.agents.session import AgentSession
    from tlon.base.config import AgentConfig
    from tlon.memory.manager import MemoryManager
    from tlon.organization.mail.mail import Mail


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

    async def _check_reflection_trigger(self) -> None:
        """Run the memory-reflection trigger check (proactive, no mail)."""
        ...


__all__ = [
    "SchedulerHost",
]
