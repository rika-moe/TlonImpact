"""Social public-health monitor.

Backpressure (bounded bus queues, session schedulers) protects the
*plumbing*; this monitor watches the *society*:

- Noise detection (real-time): a pure event consumer of MAIL_NEW. Each
  sender gets an in-memory sliding window; crossing the mail-rate
  threshold publishes an AGENT_NOISY fact (per-sender cooldown
  keeps the fact itself from becoming noise).
- Orphan-mail scan (periodic): every ``health_scan_interval_seconds``,
  every member's mailbox is scanned for thread roots older than
  ``orphan_mail_days`` with no reply in the owner's inbox, and the
  statistics are published as a MAIL_ORPHAN_SCAN fact.

Mechanism/policy split (same分工 as IdleReaper → Adam): the monitor only
publishes facts — it never stops a noisy agent and never notifies an
orphan's recipients. Deciding what to do with a fact is policy, not this
component's job: Adam's spec consumes AGENT_NOISY (noisy-circuit-breaker
→ stop the flooding member), while MAIL_ORPHAN_SCAN deliberately has no
consumer yet (see adam.py's module docstring for the rationale).

Owned by ``AgentRegistry`` (like ``IdleReaper``): the registry constructs
it with the event bus, the agent info provider (member enumeration) and
the paths resolver (mailbox locations), and runs its lifecycle from the
registry's own initialize/shutdown.
"""

import asyncio
import time
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from tlon.base.events.event import Event, EventType
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.observability.ports import OrphanMailProvider
from tlon.organization.mail.guardian_provider import GuardianAgentInfoProvider
from tlon.organization.mail.mail import address_for_member
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.resolver import RecipientResolver

if TYPE_CHECKING:
    from tlon.base.config import AgentInfoProvider
    from tlon.base.events.event_bus import EventBus

logger = get_logger(__name__)

#: Max orphan entries carried in one scan payload (payload bloat guard).
ORPHAN_REPORT_CAP = 20


def _payload_value(payload: Any, key: str) -> Any:
    """Read a field from an event payload (dict or dataclass-like object)."""
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _mail_timestamp(value: str) -> datetime | None:
    """Parse a Mail timestamp; naive values read as UTC, junk as None."""
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


async def collect_orphan_mails(
    members: dict[str, str],
    *,
    tlon_paths: TlonPaths,
    orphan_mail_days: int,
    report_cap: int = ORPHAN_REPORT_CAP,
) -> dict[str, Any]:
    """Scan every member's mailbox for unanswered old thread roots.

    An orphan is a sent thread root (``in_reply_to`` empty) that is
    older than ``orphan_mail_days`` and whose thread has no reply in
    the owner's inbox. The owner's own replies don't count.

    Read-only: mailboxes are only opened when they already exist on disk
    — a scan never creates a mailbox.

    Args:
        members: Public directory id to mailbox owner mapping; injected for tests
        tlon_paths: Mailbox layout resolver
        orphan_mail_days: Minimum thread-root age to qualify as orphan
        report_cap: Max orphan entries in the payload

    Returns:
        The scan payload: ``scanned_members`` (public ids), ``scanned_count`` (successful count),
        ``failed_members`` (public ids whose mailbox scan failed), ``orphan_count``
        (total found), ``orphans`` (capped at ``report_cap``, oldest
        first, each with mail_id/from/to/subject/age_days)
    """
    now = datetime.now(UTC)
    min_age = timedelta(days=orphan_mail_days)

    orphans: list[dict[str, Any]] = []
    scanned_members: list[str] = []
    failed_members: list[str] = []
    for public_id, member_id in sorted(members.items()):
        try:
            found = await _orphans_for(
                member_id, Mailbox(tlon_paths.mail_database_file(), member_id), now, min_age
            )
        except Exception as e:
            logger.error("orphan_scan_member_failed", member_id=member_id, error=str(e))
            failed_members.append(public_id)
            continue
        scanned_members.append(public_id)
        orphans.extend(found)

    orphans.sort(key=lambda entry: entry["age_days"], reverse=True)
    return {
        "scanned_members": scanned_members,
        "scanned_count": len(scanned_members),
        "failed_members": failed_members,
        "orphan_count": len(orphans),
        "orphans": orphans[:report_cap],
    }


async def _orphans_for(
    member_id: str,
    mailbox: Mailbox,
    now: datetime,
    min_age: timedelta,
) -> list[dict[str, Any]]:
    """One member's orphan thread roots (see :func:`collect_orphan_mails`)."""
    own_address = address_for_member(member_id)
    orphans: list[dict[str, Any]] = []
    for mail in await mailbox.sent():
        if mail.in_reply_to is not None:
            continue  # not a thread root
        timestamp = _mail_timestamp(mail.timestamp)
        if timestamp is None:
            continue
        age = now - timestamp
        if age < min_age:
            continue
        thread = await mailbox.thread_incoming(mail.thread_id)
        answered = any(
            reply.in_reply_to is not None and reply.from_address != own_address for reply in thread
        )
        if answered:
            continue
        orphans.append(
            {
                "mail_id": mail.id,
                "from": mail.from_address,
                "to": list(mail.to),
                "subject": mail.subject,
                "age_days": round(age.total_seconds() / 86400, 1),
            }
        )
    return orphans


def orphan_mail_provider(
    tlon_paths: TlonPaths,
    agent_info: "AgentInfoProvider",
    orphan_mail_days: int = 3,
) -> OrphanMailProvider:
    """Bind the orphan scan to a paths layout and a live roster.

    The delivery directory is re-read in a worker per query, including Adam
    and registered humans and excluding retired or shadowed addresses. Public
    IDs are reported; mailbox owners retain the delivery layer's internal keys.

    Returns:
        An ``OrphanMailProvider`` running the on-demand scan
    """

    resolver = RecipientResolver(GuardianAgentInfoProvider(agent_info), tlon_paths)

    def members() -> dict[str, str]:
        return {
            member["id"]: owner
            for member in resolver.list_members()
            if (owner := resolver.resolve_member(member["address"])) is not None
        }

    async def scan() -> dict[str, Any]:
        return await collect_orphan_mails(
            await asyncio.to_thread(members),
            tlon_paths=tlon_paths,
            orphan_mail_days=orphan_mail_days,
        )

    return scan


class PublicHealthMonitor:
    """Publishes social-health facts: AGENT_NOISY and MAIL_ORPHAN_SCAN.

    Fact detection only — no member state is ever changed here.

    Dependencies (constructor injected):
        - event_bus: EventBus to subscribe MAIL_NEW and publish facts
        - agent_info_provider: member enumeration (agent ids)
        - tlon_paths: mailbox directory resolution

    The thresholds are constructor knobs (same pattern as the registry's
    spawn budget): society-wide mechanism settings, not per-member config.
    """

    #: Max orphan entries carried in one MAIL_ORPHAN_SCAN payload
    ORPHAN_REPORT_CAP = ORPHAN_REPORT_CAP

    def __init__(
        self,
        event_bus: "EventBus",
        agent_info_provider: "AgentInfoProvider",
        tlon_paths: TlonPaths,
        *,
        health_scan_interval_seconds: float = 3600.0,
        orphan_mail_days: int = 3,
        noisy_window_seconds: float = 60.0,
        noisy_threshold: int = 20,
        noisy_cooldown_seconds: float = 600.0,
    ) -> None:
        self._event_bus = event_bus
        self._scan = orphan_mail_provider(tlon_paths, agent_info_provider, orphan_mail_days)

        self._health_scan_interval_seconds = health_scan_interval_seconds
        self._noisy_window_seconds = noisy_window_seconds
        self._noisy_threshold = noisy_threshold
        self._noisy_cooldown_seconds = noisy_cooldown_seconds

        # Noise detection state: per-sender sliding window of monotonic
        # send timestamps, and the last alert time
        self._windows: dict[str, deque[float]] = {}
        self._last_noisy_alert: dict[str, float] = {}

        self._subscription_id: str | None = None
        self._scan_task: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        """Subscribe to MAIL_NEW and start the periodic orphan scan."""
        if self._subscription_id is None:
            self._subscription_id = self._event_bus.subscribe(EventType.MAIL_NEW, self._on_mail_new)
        if self._scan_task is None:
            self._scan_task = asyncio.create_task(self.run())
        logger.info("public_health_monitor_initialized")

    async def shutdown(self) -> None:
        """Stop the scan task and unsubscribe from the bus."""
        if self._scan_task is not None:
            self._scan_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._scan_task
            self._scan_task = None
        if self._subscription_id is not None:
            self._event_bus.unsubscribe(self._subscription_id)
            self._subscription_id = None
        logger.info("public_health_monitor_shutdown")

    # ========== Noise detection (real-time) ==========

    async def _on_mail_new(self, event: Event) -> None:
        """Track one sent mail; publish AGENT_NOISY when a threshold trips.

        Runs in the bus dispatch context — never raises (a metrics-style
        failure must not affect bus dispatch).
        """
        try:
            await self._track_mail(event)
        except Exception as e:
            logger.warning("health_event_failed", event_type=event.type.value, error=str(e))

    async def _track_mail(self, event: Event) -> None:
        payload = event.payload
        from_address = _payload_value(payload, "from_address")
        agent_id = event.source or (str(from_address) if from_address else "unknown")

        now = time.monotonic()
        window = self._windows.setdefault(agent_id, deque())
        window.append(now)
        cutoff = now - self._noisy_window_seconds
        while window and window[0] < cutoff:
            window.popleft()

        mail_count = len(window)
        if mail_count <= self._noisy_threshold:
            return

        last_alert = self._last_noisy_alert.get(agent_id)
        if last_alert is not None and now - last_alert < self._noisy_cooldown_seconds:
            return
        self._last_noisy_alert[agent_id] = now

        logger.info(
            "agent_noisy_detected",
            agent_id=agent_id,
            mail_count=mail_count,
        )
        await self._event_bus.publish(
            Event(
                type=EventType.AGENT_NOISY,
                payload={
                    "agent_id": agent_id,
                    "window_seconds": self._noisy_window_seconds,
                    "mail_count": mail_count,
                },
                source="public_health_monitor",
            )
        )

    # ========== Orphan-mail scan (periodic) ==========

    async def run(self) -> None:
        """Periodically scan all mailboxes for orphan thread roots.

        Every ``health_scan_interval_seconds`` a full scan runs and its
        statistics are published as one MAIL_ORPHAN_SCAN fact.
        """
        while True:
            await asyncio.sleep(self._health_scan_interval_seconds)
            try:
                await self.scan_and_report()
            except Exception as e:
                logger.error("orphan_scan_failed", error=str(e))

    async def scan_and_report(self) -> None:
        """Run one orphan scan and publish the MAIL_ORPHAN_SCAN fact."""
        payload = await self.scan_orphan_mail()
        logger.info(
            "orphan_scan_completed",
            scanned_count=payload["scanned_count"],
            failed_members=payload["failed_members"],
            orphan_count=payload["orphan_count"],
        )
        await self._event_bus.publish(
            Event(
                type=EventType.MAIL_ORPHAN_SCAN,
                payload=payload,
                source="public_health_monitor",
            )
        )

    async def scan_orphan_mail(self) -> dict[str, Any]:
        """Scan every member's mailbox for unanswered old thread roots.

        The scan itself is the module-level pure collector
        (:func:`collect_orphan_mails` — shared with the ``social-health``
        observability tool); this method supplies the roster and the
        monitor's thresholds. See there for the orphan definition.

        Returns:
            The MAIL_ORPHAN_SCAN payload: ``scanned_members`` (public ids), ``scanned_count`` (successful count),
            ``failed_members`` (public ids whose mailbox scan failed),
            ``orphan_count`` (total found), ``orphans`` (capped at
            ``ORPHAN_REPORT_CAP``, oldest first, each with
            mail_id/from/to/subject/age_days)
        """
        return await self._scan()
