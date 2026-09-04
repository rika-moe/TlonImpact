"""EffectExecutor — engine-side execution of stage-declared effects (C2).

The pure stages declare their intended system-state changes as
:class:`~tlon.agents.stages.outcome.StageOutcome` data; the LoopDriver
applies the session-local fields itself and delegates the external
effects here:

- mails (``MailEffect`` -> Mail.create + MAIL_NEW event, trace-injected;
  a failure lands a postmaster-style notice in the agent's own inbox)
- the inbox cursor advance (mark-read)

The engine-side event-publish closure for tool dispatch observability
(TOOL_EXECUTE_START) also lives here.

Everything is assembled from the Agent's existing injected
dependencies — the executor never holds a reference to the Agent.
"""

from typing import TYPE_CHECKING

from tlon.agents.member_spec import MemberRule
from tlon.agents.stages.outcome import MailEffect
from tlon.agents.stages.services import EventPublisher
from tlon.agents.trigger import AgentIdentity
from tlon.base.events.event import EventType
from tlon.base.tracing.trace_context import get_current_trace, start_span
from tlon.organization.mail.mail import POSTMASTER_ADDRESS, Mail
from tlon.organization.mail.mailbox import Mailbox

if TYPE_CHECKING:
    import structlog


class EffectExecutor:
    """Executes the external effects a stage declares in its Outcome."""

    def __init__(
        self,
        identity: AgentIdentity,
        rule: MemberRule,
        mailbox: Mailbox | None,
        publish: EventPublisher,
        logger: "structlog.stdlib.BoundLogger",
    ):
        """Assemble from the Agent's injected dependencies.

        Args:
            identity: Agent identity snapshot (id/name/address)
            rule: The cognitive rule the effects are declared by
            mailbox: The agent's own mailbox (inbox cursor); None in
                bare test setups disables mark-read
            publish: Event-bus publish closure (Device.emit)
            logger: Per-agent bound logger (agent_id already bound)
        """
        self.identity = identity
        self.rule = rule
        self.publish = publish
        self._mailbox = mailbox
        self._logger = logger

    async def send_mail(self, effect: MailEffect) -> None:
        """Send a declared mail via the event bus, injecting the trace context.

        Reply headers (``in_reply_to`` / ``thread_id``) are declared by
        the stage, which derives them from the session's trigger mail;
        both default to a fresh thread. Never raises: a failure is
        logged and answered with a postmaster-style notice in the
        agent's own inbox (the same failure signal the delivery bounce
        gives), not propagated into the loop.
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
        except Exception as e:
            self._logger.error("mail_emit_failed", exc_info=True)
            await self._notify_send_failure(effect, e)

    async def _notify_send_failure(self, effect: MailEffect, error: Exception) -> None:
        """Land a send-failure notice in the agent's own inbox (issue ).

        Same style as MailDelivery's postmaster bounce: a direct inbox
        append plus one MAIL_DELIVERED, never MAIL_NEW — the notice
        path must not depend on the very publish that may have just
        failed. Fully defensive: a notice failure is logged only, and
        this path never goes through send_mail, so it cannot loop.
        """
        if self._mailbox is None:
            return
        try:
            notice = Mail.create(
                from_address=POSTMASTER_ADDRESS,
                to=[self.identity.address],
                subject=f"发送失败: {effect.subject or '（无主题）'}",
                content=(
                    "你发出的一封邮件未能进入投递队列：\n"
                    f"- 收件人：{', '.join([*effect.to, *(effect.cc or [])])}\n"
                    f"- 原因：{error}\n"
                    "请修正后重新发送。"
                ),
                in_reply_to=effect.in_reply_to,
                thread_id=effect.thread_id,
            )
            await self._mailbox.append_inbox(notice)
            await self.publish(
                EventType.MAIL_DELIVERED,
                payload={"mail": notice, "recipient": self.identity.id},
                source=self.identity.id,
            )
            self._logger.info("mail_failure_notified", notice_id=notice.id)
        except Exception:
            self._logger.error("mail_failure_notice_failed", exc_info=True)

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
