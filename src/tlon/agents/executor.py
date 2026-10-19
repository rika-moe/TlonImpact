"""EffectExecutor — engine-side execution of stage-declared effects (C2).

The pure stages declare their intended system-state changes as
:class:`~tlon.agents.stages.outcome.StageOutcome` data; the LoopDriver
applies the session-local fields itself and delegates the external
effects here:

- mails (``MailDraft`` -> PostOffice transaction, trace-injected;
  failures propagate directly into the tool result)

The engine-side event-publish closure for tool dispatch observability
(TOOL_EXECUTE_START) also lives here.

Everything is assembled from the Agent's existing injected
dependencies — the executor never holds a reference to the Agent.
"""

from typing import TYPE_CHECKING

from tlon.agents.stages.services import EventPublisher
from tlon.agents.trigger import AgentIdentity
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.member_mail import MailDraft, MemberMailService
from tlon.organization.mail.post_office import PostOffice
from tlon.organization.mail.store import StoredPost

if TYPE_CHECKING:
    import structlog


class EffectExecutor:
    """Executes the external effects a stage declares in its Outcome."""

    def __init__(
        self,
        identity: AgentIdentity,
        mailbox: Mailbox | None,
        publish: EventPublisher,
        logger: "structlog.stdlib.BoundLogger",
        mentor_cc: str | None = None,
        post_office: PostOffice | None = None,
    ) -> None:
        """Assemble from the Agent's injected dependencies.

        Args:
            identity: Agent identity snapshot (id/name/address)
            mailbox: The agent's own mailbox (used to validate reply and
            endorsement references against its contents); None disables
            mail submission
            publish: Event-bus publish closure (Device.emit)
            logger: Per-agent bound logger (agent_id already bound)
            mentor_cc: Trust ladder (docs/design/unified-member-model.md):
                a probationary member's mentor address, cc'd on every
                composed mail; None for full members
        """
        self.identity = identity
        self.publish = publish
        self._mail_service = (
            MemberMailService(identity.id, mailbox, post_office, mentor_cc)
            if mailbox is not None and post_office is not None
            else None
        )

    async def send_mail(self, effect: MailDraft, request_id: str | None = None) -> StoredPost:
        if self._mail_service is None:
            raise RuntimeError("This member has no mail service")
        return await self._mail_service.submit(effect, request_id)
