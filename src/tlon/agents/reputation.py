"""Reputation derivation for the observability tools ().

Reputation is derived, never stored: every number here is recomputed
from the society's mail files on each call — the endorsement signal
(``Mail.endorsing``) is the only raw material. The computation reads
the organization mail domain, so it lives in the agents layer; the
tools layer consumes it through the ``ReputationProvider`` port
(``tlon.tools.observability.ports``).

Read-only discipline: other members' mailboxes are only opened when
they already exist on disk — a reputation query never creates a
mailbox. The full-society scan is acceptable at the Republic's scale
(a handful of members).
"""

from collections.abc import Callable
from typing import Any

from tlon.agents.mail_stats import collect_mail_stats
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import address_for_member
from tlon.organization.mail.mailbox import Mailbox
from tlon.tools.observability.ports import ReputationProvider

#: Cap on the top-endorsed-mails list (payload bloat guard).
TOP_ENDORSED_CAP = 5


async def collect_reputation(
    agent_id: str,
    *,
    tlon_paths: TlonPaths,
    member_ids: list[str],
) -> dict[str, Any]:
    """Derive one member's reputation from the society's mail files.

    - ``endorsements_given``: mails in the member's own sent file that
      carry an ``endorsing`` reference.
    - ``endorsements_received``: across every member's sent file, the
      endorsement mails whose referenced mail was authored by the
      target; deduplicated by ``(endorser, mail_id)`` — one endorser
      endorsing the same mail repeatedly counts once.
    - ``top_endorsed_mails``: the target's mails endorsed by the most
      distinct endorsers (mail_id / subject / endorser count), top
      ``TOP_ENDORSED_CAP``.
    - ``mail_behavior``: a summary embedded from
      :func:`collect_mail_stats` (volumes, reply rate, average
      first-reply latency).

    Args:
        agent_id: Reputation subject (agent id or ``human:<username>``)
        tlon_paths: Mailbox layout resolver
        member_ids: Every society member id to scan (agents + known
            humans); injected to keep the collector testable

    Returns:
        JSON-safe reputation dict
    """
    own_address = address_for_member(agent_id)

    given = 0
    given = sum(
        1
        for mail in await Mailbox(tlon_paths.mail_database_file(), agent_id).sent()
        if mail.endorsing
    )

    received: set[tuple[str, str]] = set()  # (endorser member id, endorsed mail id)
    endorsers_by_mail: dict[str, set[str]] = {}
    subject_by_mail: dict[str, str | None] = {}
    for member_id in member_ids:
        mailbox = Mailbox(tlon_paths.mail_database_file(), member_id)
        for mail in await mailbox.sent():
            if not mail.endorsing:
                continue
            endorsed = await mailbox.find(mail.endorsing)
            if endorsed is None or endorsed.from_address != own_address:
                continue
            received.add((member_id, mail.endorsing))
            endorsers_by_mail.setdefault(mail.endorsing, set()).add(member_id)
            subject_by_mail.setdefault(mail.endorsing, endorsed.subject)

    top_endorsed = [
        {
            "mail_id": mail_id,
            "subject": subject_by_mail.get(mail_id),
            "endorsers": len(endorsers),
        }
        for mail_id, endorsers in sorted(
            endorsers_by_mail.items(),
            key=lambda item: len(item[1]),
            reverse=True,
        )[:TOP_ENDORSED_CAP]
    ]

    stats = await collect_mail_stats(agent_id, tlon_paths=tlon_paths)
    correspondents = stats["correspondents"]
    threads_initiated = sum(c["threads_initiated"] for c in correspondents.values())
    threads_replied = sum(c["threads_replied"] for c in correspondents.values())
    latencies = [
        c["avg_first_reply_seconds"]
        for c in correspondents.values()
        if c["avg_first_reply_seconds"] is not None
    ]
    mail_behavior = {
        "total_sent": stats["total_sent"],
        "total_received": stats["total_received"],
        "reply_rate": (threads_replied / threads_initiated if threads_initiated else None),
        "avg_first_reply_seconds": (sum(latencies) / len(latencies) if latencies else None),
    }

    return {
        "agent_id": agent_id,
        "address": own_address,
        "endorsements_given": given,
        "endorsements_received": len(received),
        "top_endorsed_mails": top_endorsed,
        "mail_behavior": mail_behavior,
    }


def reputation_provider(
    tlon_paths: TlonPaths,
    member_ids: Callable[[], list[str]],
) -> ReputationProvider:
    """Bind the reputation collector to a paths layout ().

    Args:
        tlon_paths: Mailbox layout resolver
        member_ids: Society member enumeration (agents + known humans),
            called per query so a reputation read sees the current roster

    Returns:
        A ``ReputationProvider`` deriving reputation for any member id
        against the given ``TlonPaths``
    """

    async def collect(agent_id: str) -> dict[str, Any]:
        return await collect_reputation(agent_id, tlon_paths=tlon_paths, member_ids=member_ids())

    return collect
