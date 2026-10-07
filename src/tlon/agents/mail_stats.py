"""Mail social statistics provider for the observability tools ().

The computation reads the organization mail domain, so it lives in the
agents layer; the tools layer consumes it through the ``MailStatsProvider``
port (``tlon.tools.observability.ports``). Read-only: only the subject
agent's own mailbox is scanned (``Mailbox.sent`` / ``Mailbox.inbox``).
"""

from datetime import UTC, datetime
from typing import Any

from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import ALL_ADDRESS, address_for_member
from tlon.organization.mail.mailbox import Mailbox
from tlon.tools.observability.ports import MailStatsProvider

#: Cap on the never-replied address list (payload bloat guard).
NEVER_REPLIED_CAP = 20


def _parse_timestamp(value: str) -> datetime:
    """Parse an ISO 8601 mail timestamp; naive values are read as UTC."""
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def _correspondent(stats: dict[str, dict[str, Any]], address: str) -> dict[str, Any]:
    return stats.setdefault(
        address,
        {
            "sent": 0,
            "received": 0,
            "threads_initiated": 0,
            "threads_replied": 0,
            "_latencies": [],
        },
    )


async def collect_mail_stats(agent_id: str, *, tlon_paths: TlonPaths) -> dict[str, Any]:
    """Compute one agent's mail social statistics from its own mailbox.

    Aggregation rules:
    - A correspondent is any address the agent mailed (to/cc) or received
      from; the broadcast address ``all@tlon.internal`` and the agent
      itself are excluded.
    - A thread is "initiated" by the agent when the thread root
      (``mail.id == mail.thread_id``) sits in the agent's sent file.
    - A thread is "replied" when the inbox holds a mail in that thread
      from a root recipient; first-reply latency is the gap between the
      thread root's send time and that correspondent's first reply.
    """
    mailbox = Mailbox(tlon_paths.member_mail_dir(agent_id))
    own_address = address_for_member(agent_id)
    sent = await mailbox.sent()
    inbox = await mailbox.inbox()

    stats: dict[str, dict[str, Any]] = {}
    # thread_id -> (root send time, root recipient addresses)
    roots: dict[str, tuple[datetime, set[str]]] = {}
    for mail in sent:
        recipients = {
            address for address in [*mail.to, *mail.cc] if address not in (ALL_ADDRESS, own_address)
        }
        for address in recipients:
            _correspondent(stats, address)["sent"] += 1
        if mail.id == mail.thread_id and recipients:
            roots[mail.thread_id] = (_parse_timestamp(mail.timestamp), recipients)
            for address in recipients:
                _correspondent(stats, address)["threads_initiated"] += 1

    # thread_id -> {correspondent: first reply time}
    first_replies: dict[str, dict[str, datetime]] = {}
    for mail in inbox:
        sender = mail.from_address
        if sender in (ALL_ADDRESS, own_address):
            continue
        _correspondent(stats, sender)["received"] += 1
        root = roots.get(mail.thread_id)
        if root is None or sender not in root[1]:
            continue
        ts = _parse_timestamp(mail.timestamp)
        replies = first_replies.setdefault(mail.thread_id, {})
        if sender not in replies or ts < replies[sender]:
            replies[sender] = ts

    for thread_id, replies in first_replies.items():
        root_ts = roots[thread_id][0]
        for address, reply_ts in replies.items():
            entry = stats[address]
            entry["threads_replied"] += 1
            entry["_latencies"].append((reply_ts - root_ts).total_seconds())

    correspondents: dict[str, dict[str, Any]] = {}
    for address, entry in sorted(
        stats.items(), key=lambda item: item[1]["sent"] + item[1]["received"], reverse=True
    ):
        latencies: list[float] = entry["_latencies"]
        correspondents[address] = {
            "sent": entry["sent"],
            "received": entry["received"],
            "threads_initiated": entry["threads_initiated"],
            "threads_replied": entry["threads_replied"],
            "avg_first_reply_seconds": (sum(latencies) / len(latencies) if latencies else None),
        }

    never_replied = sorted(address for address, entry in stats.items() if entry["received"] == 0)
    return {
        "agent_id": agent_id,
        "address": own_address,
        "total_sent": len(sent),
        "total_received": len(inbox),
        "correspondents": correspondents,
        "never_replied": never_replied[:NEVER_REPLIED_CAP],
        "never_replied_total": len(never_replied),
    }


def mail_stats_provider(tlon_paths: TlonPaths) -> MailStatsProvider:
    """Bind the mail-stats collector to a paths layout ().

    Returns:
        A ``MailStatsProvider`` computing stats for any agent id against
        the given ``TlonPaths``
    """

    async def collect(agent_id: str) -> dict[str, Any]:
        return await collect_mail_stats(agent_id, tlon_paths=tlon_paths)

    return collect
