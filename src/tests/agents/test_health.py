"""PublicHealthMonitor — social public-health facts (docs/design/mail.md).

Pins the two fact lines: noise detection (AGENT_NOISY sliding window +
cooldown) and the periodic orphan-mail scan
(MAIL_ORPHAN_SCAN — old unanswered thread roots; fresh mail
and answered threads excluded; report truncation).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tlon.agents.health import PublicHealthMonitor
from tlon.base.config import AgentConfig, AgentInfoProvider
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail, address_for_member
from tlon.organization.mail.mailbox import Mailbox


class _AgentInfoStub(AgentInfoProvider):
    """Read-only member enumeration for the monitor."""

    def __init__(self, agent_ids: list[str]):
        self._agents = {aid: AgentConfig(id=aid, name=aid) for aid in agent_ids}

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._agents.get(agent_id)

    def get_all_ids(self) -> set[str]:
        return set(self._agents)


@pytest.fixture
async def bus():
    b = EventBus()
    await b.initialize()
    yield b
    await b.shutdown()


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out waiting for condition")


def _monitor(bus: EventBus, tmp_path: Path, agent_ids=None, **knobs) -> PublicHealthMonitor:
    return PublicHealthMonitor(
        event_bus=bus,
        agent_info_provider=_AgentInfoStub(agent_ids or []),
        tlon_paths=TlonPaths(home=tmp_path),
        **knobs,
    )


async def _send(bus: EventBus, sender: str, to=None, content: str = "hi") -> None:
    mail = Mail.create(
        from_address=address_for_member(sender), to=to or ["bob@tlon.internal"], content=content
    )
    await bus.publish(Event(type=EventType.MAIL_NEW, payload=mail, source=sender))


def _mail(timestamp: datetime, **kwargs) -> Mail:
    mail = Mail.create(**kwargs)
    mail.timestamp = timestamp.isoformat()
    return mail


class TestNoiseDetection:
    async def test_burst_over_threshold_publishes_fact(self, bus, tmp_path):
        monitor = _monitor(bus, tmp_path, noisy_threshold=3, noisy_window_seconds=60)
        noisy: list[Event] = []
        bus.subscribe(EventType.AGENT_NOISY, lambda e: noisy.append(e))
        await monitor.initialize()

        for _ in range(4):
            await _send(bus, "agent-a")
        await _wait_for(lambda: len(noisy) == 1)

        payload = noisy[0].payload
        assert payload["agent_id"] == "agent-a"
        assert payload["mail_count"] == 4
        assert payload["window_seconds"] == 60
        await monitor.shutdown()

    async def test_below_threshold_stays_quiet(self, bus, tmp_path):
        monitor = _monitor(bus, tmp_path, noisy_threshold=20)
        noisy: list[Event] = []
        bus.subscribe(EventType.AGENT_NOISY, lambda e: noisy.append(e))
        await monitor.initialize()

        for _ in range(3):
            await _send(bus, "agent-a")
        await asyncio.sleep(0.1)
        assert noisy == []
        await monitor.shutdown()

    async def test_cooldown_suppresses_repeat_alerts(self, bus, tmp_path):
        monitor = _monitor(bus, tmp_path, noisy_threshold=1, noisy_cooldown_seconds=600)
        noisy: list[Event] = []
        bus.subscribe(EventType.AGENT_NOISY, lambda e: noisy.append(e))
        await monitor.initialize()

        for _ in range(5):
            await _send(bus, "agent-a")
        await asyncio.sleep(0.1)
        assert len(noisy) == 1
        await monitor.shutdown()

    async def test_window_expiry_clears_count(self, bus, tmp_path):
        monitor = _monitor(bus, tmp_path, noisy_threshold=2, noisy_window_seconds=0.05)
        noisy: list[Event] = []
        bus.subscribe(EventType.AGENT_NOISY, lambda e: noisy.append(e))
        await monitor.initialize()

        await _send(bus, "agent-a")
        await asyncio.sleep(0.1)  # let the window expire
        await _send(bus, "agent-a")
        await _send(bus, "agent-a")
        await asyncio.sleep(0.1)
        # window held only the last 2 mails: 2 <= threshold, no fact
        assert noisy == []
        await monitor.shutdown()


class TestOrphanScan:
    async def test_old_unanswered_thread_root_is_orphan(self, bus, tmp_path):
        paths = TlonPaths(home=tmp_path)
        mailbox = Mailbox(paths.mail_database_file(), "alice")
        old = datetime.now(UTC) - timedelta(days=4)
        root = _mail(
            old,
            from_address="alice@tlon.internal",
            to=["bob@tlon.internal"],
            content="question",
            subject="help",
        )
        await mailbox.append_sent(root)

        monitor = _monitor(bus, tmp_path, agent_ids=["alice"], orphan_mail_days=3)
        payload = await monitor.scan_orphan_mail()

        assert payload["scanned_members"] == 1
        assert payload["orphan_count"] == 1
        orphan = payload["orphans"][0]
        assert orphan["mail_id"] == root.id
        assert orphan["from"] == "alice@tlon.internal"
        assert orphan["to"] == ["bob@tlon.internal"]
        assert orphan["subject"] == "help"
        assert orphan["age_days"] >= 3

    async def test_replied_thread_is_not_orphan(self, bus, tmp_path):
        paths = TlonPaths(home=tmp_path)
        mailbox = Mailbox(paths.mail_database_file(), "alice")
        old = datetime.now(UTC) - timedelta(days=4)
        root = _mail(
            old,
            from_address="alice@tlon.internal",
            to=["bob@tlon.internal"],
            content="question",
        )
        await mailbox.append_sent(root)
        reply = _mail(
            old + timedelta(hours=1),
            from_address="bob@tlon.internal",
            to=["alice@tlon.internal"],
            content="answer",
            in_reply_to=root.id,
            thread_id=root.thread_id,
        )
        await mailbox.append_inbox(reply)

        monitor = _monitor(bus, tmp_path, agent_ids=["alice"], orphan_mail_days=3)
        payload = await monitor.scan_orphan_mail()
        assert payload["orphan_count"] == 0

    async def test_own_reply_does_not_count(self, bus, tmp_path):
        paths = TlonPaths(home=tmp_path)
        mailbox = Mailbox(paths.mail_database_file(), "alice")
        old = datetime.now(UTC) - timedelta(days=4)
        root = _mail(
            old,
            from_address="alice@tlon.internal",
            to=["bob@tlon.internal"],
            content="question",
        )
        await mailbox.append_sent(root)
        # Alice's own follow-up in her inbox (e.g. self-cc) is not an answer
        own = _mail(
            old + timedelta(hours=1),
            from_address="alice@tlon.internal",
            to=["bob@tlon.internal"],
            content="bump",
            in_reply_to=root.id,
            thread_id=root.thread_id,
        )
        await mailbox.append_inbox(own)

        monitor = _monitor(bus, tmp_path, agent_ids=["alice"], orphan_mail_days=3)
        payload = await monitor.scan_orphan_mail()
        assert payload["orphan_count"] == 1

    async def test_fresh_mail_is_not_orphan(self, bus, tmp_path):
        paths = TlonPaths(home=tmp_path)
        mailbox = Mailbox(paths.mail_database_file(), "alice")
        recent = datetime.now(UTC) - timedelta(hours=1)
        await mailbox.append_sent(
            _mail(
                recent,
                from_address="alice@tlon.internal",
                to=["bob@tlon.internal"],
                content="question",
            )
        )

        monitor = _monitor(bus, tmp_path, agent_ids=["alice"], orphan_mail_days=3)
        payload = await monitor.scan_orphan_mail()
        assert payload["orphan_count"] == 0

    async def test_report_is_capped_and_count_is_total(self, bus, tmp_path):
        paths = TlonPaths(home=tmp_path)
        mailbox = Mailbox(paths.mail_database_file(), "alice")
        old = datetime.now(UTC) - timedelta(days=4)
        for i in range(25):
            await mailbox.append_sent(
                _mail(
                    old,
                    from_address="alice@tlon.internal",
                    to=["bob@tlon.internal"],
                    content=f"question {i}",
                )
            )

        monitor = _monitor(bus, tmp_path, agent_ids=["alice"], orphan_mail_days=3)
        payload = await monitor.scan_orphan_mail()
        assert payload["orphan_count"] == 25
        assert len(payload["orphans"]) == PublicHealthMonitor.ORPHAN_REPORT_CAP

    async def test_scan_and_report_publishes_fact(self, bus, tmp_path):
        paths = TlonPaths(home=tmp_path)
        mailbox = Mailbox(paths.mail_database_file(), "alice")
        old = datetime.now(UTC) - timedelta(days=4)
        await mailbox.append_sent(
            _mail(
                old,
                from_address="alice@tlon.internal",
                to=["bob@tlon.internal"],
                content="question",
            )
        )

        monitor = _monitor(bus, tmp_path, agent_ids=["alice"], orphan_mail_days=3)
        scans: list[Event] = []
        bus.subscribe(EventType.MAIL_ORPHAN_SCAN, lambda e: scans.append(e))

        await monitor.scan_and_report()
        await _wait_for(lambda: len(scans) == 1)
        payload = scans[0].payload
        assert payload["scanned_members"] == 1
        assert payload["orphan_count"] == 1
