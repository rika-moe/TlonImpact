"""Mailbox reader for the searchMails tool (issue ).

Reading the mailbox touches the organization mail domain, so it lives in
the agents layer; the tools layer consumes it through the
``MailboxMailsProvider`` port (``tlon.tools.mail.ports``) — same pattern
as ``tlon.agents.mail_stats`` (). Read-only: only the subject agent's
own mailbox is read (``Mailbox.inbox`` / ``Mailbox.sent``); no other
member's mailbox is reachable through this provider.
"""

from typing import Any

from tlon.base.paths import TlonPaths
from tlon.organization.mail.mailbox import Mailbox
from tlon.tools.mail.ports import MailboxMailsProvider


async def read_own_mails(agent_id: str, *, tlon_paths: TlonPaths) -> list[dict[str, Any]]:
    """Read one agent's own mails (inbox + sent) as JSON-safe dicts.

    Each dict is ``Mail.to_dict()`` plus a ``box`` marker
    (``"inbox"`` / ``"sent"``) so callers can tell which side of the
    correspondence a mail belongs to.
    """
    mailbox = Mailbox(tlon_paths.member_mail_dir(agent_id))
    mails: list[dict[str, Any]] = []
    for box, folder in (("inbox", await mailbox.inbox()), ("sent", await mailbox.sent())):
        for mail in folder:
            mails.append({"box": box, **mail.to_dict()})
    return mails


def mailbox_mails_provider(tlon_paths: TlonPaths) -> MailboxMailsProvider:
    """Bind the mailbox reader to a paths layout ().

    Returns:
        A ``MailboxMailsProvider`` reading any agent's own mailbox against
        the given ``TlonPaths``
    """

    async def read(agent_id: str) -> list[dict[str, Any]]:
        return await read_own_mails(agent_id, tlon_paths=tlon_paths)

    return read
