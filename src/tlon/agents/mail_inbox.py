"""Mailbox unread reader and read-marker for the checkInbox / markRead tools.

Reading and marking the mailbox touches the organization mail domain, so
both live in the agents layer; the tools layer consumes them through the
``UnreadMailsProvider`` / ``MarkReadActor`` ports
(``tlon.tools.mail.ports``) — same pattern as ``tlon.agents.mail_search``.
Only the subject agent's own mailbox is reachable through these closures.
"""

from typing import Any

from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import is_external_address
from tlon.organization.mail.mailbox import Mailbox
from tlon.tools.mail.ports import MarkReadActor, UnreadMailsProvider


async def read_own_unread(agent_id: str, *, tlon_paths: TlonPaths) -> list[dict[str, Any]]:
    """Read one agent's unread inbox mails as JSON-safe dicts.

    Each dict is ``Mail.to_dict()`` plus an ``external`` flag
    (docs/design/mail.md 「外部来信与信任面」) derived from the sender
    address domain — the trust marker travels with the data into tool
    results. Order is chronological (oldest first), matching
    ``Mailbox.unread`` — the processing order ADR 0028 expects.
    """
    mailbox = Mailbox(tlon_paths.member_mail_dir(agent_id))
    return [
        {"external": is_external_address(mail.from_address), **mail.to_dict()}
        for mail in await mailbox.unread()
    ]


def unread_mails_provider(tlon_paths: TlonPaths) -> UnreadMailsProvider:
    """Bind the unread reader to a paths layout.

    Returns:
        An ``UnreadMailsProvider`` reading any agent's unread inbox mail
        against the given ``TlonPaths``
    """

    async def read(agent_id: str) -> list[dict[str, Any]]:
        return await read_own_unread(agent_id, tlon_paths=tlon_paths)

    return read


async def mark_own_read(
    agent_id: str, mail_ids: list[str], *, tlon_paths: TlonPaths
) -> dict[str, list[str]]:
    """Mark one agent's own inbox mails read, reporting per-id outcomes.

    Look-before-write: an id that is not in the inbox is reported as
    ``not_found`` and never reaches the cursor; an id already in the
    cursor is reported as ``already_read`` (``Mailbox.mark_read`` itself
    is idempotent, the split exists so the agent can tell what happened).
    """
    mailbox = Mailbox(tlon_paths.member_mail_dir(agent_id))
    inbox_ids = {mail.id for mail in await mailbox.inbox()}
    unread_ids = {mail.id for mail in await mailbox.unread()}
    report: dict[str, list[str]] = {"marked": [], "already_read": [], "not_found": []}
    for mail_id in dict.fromkeys(mail_ids):
        if mail_id not in inbox_ids:
            report["not_found"].append(mail_id)
        elif mail_id not in unread_ids:
            report["already_read"].append(mail_id)
        else:
            await mailbox.mark_read(mail_id)
            report["marked"].append(mail_id)
    return report


def mark_read_actor(tlon_paths: TlonPaths) -> MarkReadActor:
    """Bind the read-marker to a paths layout.

    Returns:
        A ``MarkReadActor`` marking any agent's own inbox mail read
        against the given ``TlonPaths``
    """

    async def mark(agent_id: str, mail_ids: list[str]) -> dict[str, list[str]]:
        return await mark_own_read(agent_id, mail_ids, tlon_paths=tlon_paths)

    return mark
