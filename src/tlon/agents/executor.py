"""EffectExecutor — engine-side execution of stage-declared effects (C2).

The pure stages declare their intended system-state changes as
:class:`~tlon.agents.stages.outcome.StageOutcome` data; the LoopDriver
applies the session-local fields itself and delegates the external
effects here:

- mails (``MailEffect`` -> Mail.create + MAIL_NEW event, trace-injected)
- the completed-interaction memory save (gated by the loop's
  save-interaction flag)
- the inbox cursor advance (mark-read)

The engine-side event-publish closure for tool dispatch observability
(TOOL_EXECUTE_START) also lives here.

Everything is assembled from the Agent's existing injected
dependencies — the executor never holds a reference to the Agent.
"""

from typing import TYPE_CHECKING, Any

from tlon.agents.member_spec import MemberRule
from tlon.agents.stages.outcome import MailEffect
from tlon.agents.stages.services import EventPublisher
from tlon.agents.trigger import AgentIdentity
from tlon.base.events.event import EventType
from tlon.base.llm_client import LLMResponse
from tlon.base.tracing.trace_context import get_current_trace, start_span
from tlon.memory.manager import MemoryManager
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox

if TYPE_CHECKING:
    import structlog

    from tlon.agents.reflection import ReflectionEngine


class EffectExecutor:
    """Executes the external effects a stage declares in its Outcome."""

    def __init__(
        self,
        identity: AgentIdentity,
        rule: MemberRule,
        memory: MemoryManager,
        reflection: "ReflectionEngine",
        mailbox: Mailbox | None,
        publish: EventPublisher,
        logger: "structlog.stdlib.BoundLogger",
    ):
        """Assemble from the Agent's injected dependencies.

        Args:
            identity: Agent identity snapshot (id/name/address)
            rule: The cognitive rule (the save-interaction gate is read
                here, from its loop)
            memory: Memory manager for the interaction save
            reflection: Reflection engine (interaction counter)
            mailbox: The agent's own mailbox (inbox cursor); None in
                bare test setups disables mark-read
            publish: Event-bus publish closure (Device.emit)
            logger: Per-agent bound logger (agent_id already bound)
        """
        self.identity = identity
        self.rule = rule
        self.publish = publish
        self._memory = memory
        self._reflection = reflection
        self._mailbox = mailbox
        self._logger = logger

    async def send_mail(self, effect: MailEffect) -> None:
        """Send a declared mail via the event bus, injecting the trace context.

        Reply headers (``in_reply_to`` / ``thread_id``) are declared by
        the stage, which derives them from the session's trigger mail;
        both default to a fresh thread. Never raises: a mail failure is
        logged, not propagated into the loop.
        """
        try:
            mail = Mail.create(
                from_address=self.identity.address,
                to=effect.to,
                cc=effect.cc,
                subject=effect.subject,
                content=effect.content,
                in_reply_to=effect.in_reply_to,
                thread_id=effect.thread_id,
            )
            with start_span(
                "mail.send",
                source=self.identity.id,
                attributes={"mail_id": mail.id, "to": mail.to},
            ):
                current = get_current_trace()
                await self.publish(
                    EventType.MAIL_NEW,
                    payload=mail,
                    source=self.identity.id,
                    trace_id=current.trace_id if current else None,
                    span_id=current.span_id if current else None,
                    parent_span_id=current.parent_span_id if current else None,
                )
        except Exception:
            self._logger.error("mail_emit_failed", exc_info=True)

    async def save_interaction(
        self, final_response: str, llm_response: LLMResponse, tool_results: list[Any]
    ) -> None:
        """Persist a completed interaction (gated by the loop's save_interaction)."""
        assert self.rule.loop is not None, "EffectExecutor requires a cognitive rule"
        if not self.rule.loop.save_interaction:
            return
        try:
            msg = {
                "sender_id": self.identity.id,
                "sender_name": self.identity.name,
                "sender_type": "agent",
                "content": final_response,
            }
            await self._memory.add_interaction(msg, llm_response, tool_results)
            self._reflection.note_interaction()
        except Exception as e:
            self._logger.error("memory_save_failed", error=str(e), exc_info=True)

    async def mark_mail_read(self, mail: Mail | None) -> None:
        """Advance the inbox cursor once a mail's session has completed.

        Called on every session completion (success, error or timeout
        alike — the mail has been processed). Never raises: a cursor
        failure must not break session cleanup.
        """
        if mail is None or self._mailbox is None:
            return
        try:
            await self._mailbox.mark_read(mail.id)
        except Exception as e:
            self._logger.error("mail_mark_read_failed", mail_id=mail.id, error=str(e))
