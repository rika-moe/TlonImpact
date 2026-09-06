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

        Reply headers are declared by the stage: ``in_reply_to`` is the
        model's explicit choice or the session's trigger mail, and the
        ``thread_id`` declared alongside is only the trigger-derived
        fallback — the thread must follow the *referenced* mail, which
        only a mailbox read can establish (stages stay I/O-free). The
        reference is validated against the agent's own mailbox: a mail
        referencing an id the mailbox does not hold (typically a
        hallucinated id) is not sent at all, instead of shipping with a
        dangling reference. Both headers default to a fresh thread.
        Never raises: a failure is logged and answered with a
        postmaster-style notice in the agent's own inbox (the same
        failure signal the delivery bounce gives), not propagated into
        the loop.
        """
        try:
            in_reply_to, thread_id = await self._resolve_reply_headers(effect)
            endorsing = await self._resolve_endorsing(effect)
            mail = Mail.create(
                from_address=self.identity.address,
                to=effect.to,
                cc=effect.cc,
                subject=effect.subject,
                content=effect.content,
                in_reply_to=in_reply_to,
                thread_id=thread_id,
                endorsing=endorsing,
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

    async def _resolve_reply_headers(self, effect: MailEffect) -> tuple[str | None, str | None]:
        """Validate the reply reference and derive the thread from it.

        The referenced mail is looked up in the agent's own mailbox
        (inbox + sent — the only mails an agent can legitimately reply
        to); the reply joins the referenced mail's thread. Without a
        mailbox (bare test setups) the declared headers pass through
        unresolved.

        Raises:
            ValueError: The referenced mail is not in the agent's
                mailbox (typically a hallucinated id)
        """
        if effect.in_reply_to is None or self._mailbox is None:
            return effect.in_reply_to, effect.thread_id
        referenced = await self._mailbox.find(effect.in_reply_to)
        if referenced is None:
            raise ValueError(
                f"in_reply_to 引用的邮件不在你的邮箱中: {effect.in_reply_to}"
            )
        return effect.in_reply_to, referenced.thread_id

    async def _resolve_endorsing(self, effect: MailEffect) -> str | None:
        """Validate the endorsement reference (issue ).

        Same hallucination guard as the reply reference: the endorsed
        mail must exist in the agent's own mailbox, and it must not be
        the agent's own mail — self-endorsement is reputation
        self-puffery and is rejected. Without a mailbox (bare test
        setups) the declared reference passes through unresolved.

        Raises:
            ValueError: The endorsed mail is not in the agent's
                mailbox, or it was written by the agent itself
        """
        if effect.endorsing is None or self._mailbox is None:
            return effect.endorsing
        endorsed = await self._mailbox.find(effect.endorsing)
        if endorsed is None:
            raise ValueError(
                f"endorsing 致谢的邮件不在你的邮箱中: {effect.endorsing}"
            )
        if endorsed.from_address == self.identity.address:
            raise ValueError(
                f"不能致谢自己写的邮件: {effect.endorsing}"
            )
        return effect.endorsing

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
