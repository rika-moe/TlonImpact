"""Declared cron mails preserve static content and isolate delivery failures."""

import asyncio
import threading
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
import yaml

from tests.mail_helpers import make_office
from tlon.agents.cron import CronEntry, CronScheduler
from tlon.base.config import AgentConfig, AgentInfoProvider
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import (
    POSTMASTER_ADDRESS,
    Mail,
    address_for_member,
)
from tlon.organization.mail.store import MailStore

REPORTER_ID = "newspaper"


class _AgentInfoStub(AgentInfoProvider):
    """Read-only member enumeration for the scheduler."""

    def __init__(self, agent_ids: list[str]) -> None:
        self._agents = {aid: AgentConfig(id=aid, name=aid) for aid in agent_ids}

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._agents.get(agent_id)

    def get_all_ids(self) -> set[str]:
        return set(self._agents)


@pytest.fixture
async def bus() -> AsyncIterator[EventBus]:
    b = EventBus()
    await b.initialize()
    yield b
    await b.shutdown()


async def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out waiting for condition")


def _scheduler(
    bus: EventBus,
    tmp_path: Path,
    agent_ids: list[str] | None = None,
) -> CronScheduler:
    ids = agent_ids if agent_ids is not None else [REPORTER_ID]
    return CronScheduler(
        event_bus=bus,
        agent_info_provider=_AgentInfoStub(ids),
        post_office=make_office(bus, tmp_path, ids),
        tlon_paths=TlonPaths(home=tmp_path),
        )


def _make_human(tmp_path: Path, username: str) -> None:
    """A "known human" is registered in the persistent mail store."""
    MailStore(TlonPaths(home=tmp_path).mail_database_file()).register_member(f"human:{username}")


def _write_cron_yaml(tmp_path: Path, entries: object) -> None:
    (tmp_path / "cron.yaml").write_text(yaml.dump(entries), encoding="utf-8")


class TestStaticTick:
    async def test_tick_preserves_declared_mail_without_human_lookup(
        self, bus: EventBus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scheduler = _scheduler(bus, tmp_path)
        content = "发布任务。\n{{humans}} 保持原样。\n"
        entry = CronEntry("daily-paper", REPORTER_ID, "publish", content, interval_seconds=60)
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))

        def forbidden_store(*args: object, **kwargs: object) -> MailStore:
            raise AssertionError("agent task must not enumerate humans")

        monkeypatch.setattr("tlon.agents.cron.MailStore", forbidden_store)
        mail = await scheduler.tick(entry)

        assert mail is not None
        await _wait_for(lambda: len(sent) == 1)
        assert sent[0].payload == mail
        assert sent[0].source == POSTMASTER_ADDRESS
        assert mail.from_address == POSTMASTER_ADDRESS
        assert mail.to == [address_for_member(REPORTER_ID)]
        assert mail.subject == "publish"
        assert mail.content == content

    async def test_unknown_human_skipped(self, bus: EventBus, tmp_path: Path) -> None:
        scheduler = _scheduler(bus, tmp_path)
        entry = CronEntry("missing-reader", "human:ghost", "x", "y", interval_seconds=60)
        assert await scheduler.tick(entry) is None

    async def test_human_lookup_runs_off_event_loop(
        self, bus: EventBus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_human(tmp_path, "alice")
        scheduler = _scheduler(bus, tmp_path)
        loop_thread = threading.get_ident()
        original = MailStore.known_human_ids

        def checked_roster(store: MailStore) -> list[str]:
            assert threading.get_ident() != loop_thread
            return original(store)

        monkeypatch.setattr(MailStore, "known_human_ids", checked_roster)
        entry = CronEntry("reader", "human:alice", "x", "y", interval_seconds=60)
        assert await scheduler.tick(entry) is not None


class TestDeclaredEntries:
    async def test_interval_entry_fires_from_cron_yaml(self, bus: EventBus, tmp_path: Path) -> None:
        _write_cron_yaml(
            tmp_path,
            [
                {
                    "name": "staging-check",
                    "to": REPORTER_ID,
                    "subject": "定期检查 staging 提案",
                    "content": "查一下 prototypes-staging 有没有待批准的提案。",
                    "interval_seconds": 0.02,
                }
            ],
        )
        scheduler = _scheduler(bus, tmp_path)
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))

        await scheduler.initialize()
        await _wait_for(lambda: len(sent) >= 2)  # fires repeatedly, not once

        mail: Mail = sent[0].payload
        assert sent[0].source == POSTMASTER_ADDRESS
        assert mail.from_address == POSTMASTER_ADDRESS
        assert mail.to == [address_for_member(REPORTER_ID)]
        assert mail.subject == "定期检查 staging 提案"
        assert "prototypes-staging" in mail.content
        await scheduler.shutdown()

    async def test_declared_entry_to_known_human(self, bus: EventBus, tmp_path: Path) -> None:
        _make_human(tmp_path, "alice")
        _write_cron_yaml(
            tmp_path,
            [
                {
                    "name": "weekly-report",
                    "to": "human:alice",
                    "subject": "周报",
                    "content": "这是本周社会动态。",
                    "interval_seconds": 0.02,
                }
            ],
        )
        scheduler = _scheduler(bus, tmp_path, agent_ids=[])
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))

        await scheduler.initialize()
        await _wait_for(lambda: len(sent) == 1)

        mail: Mail = sent[0].payload
        assert mail.to == [address_for_member("human:alice")]
        await scheduler.shutdown()

    async def test_cron_schedule_computes_next_fire(self, bus: EventBus, tmp_path: Path) -> None:
        _write_cron_yaml(
            tmp_path,
            [
                {
                    "name": "morning-report",
                    "to": REPORTER_ID,
                    "subject": "晨报",
                    "content": "早安。",
                    "cron": "* * * * *",
                }
            ],
        )
        scheduler = _scheduler(bus, tmp_path)

        await scheduler.initialize()

        entry = next(e for e in scheduler._entries if e.name == "morning-report")
        assert entry.cron == "* * * * *"
        delay = scheduler._seconds_until_next(entry)
        assert 0.0 < delay <= 60.0
        await scheduler.shutdown()

    async def test_invalid_entry_isolated(self, bus: EventBus, tmp_path: Path) -> None:
        _write_cron_yaml(
            tmp_path,
            [
                {"name": "missing-to", "subject": "x", "content": "y", "interval_seconds": 1},
                {
                    "name": "bad-cron",
                    "to": REPORTER_ID,
                    "subject": "x",
                    "content": "y",
                    "cron": "not a cron",
                },
                {
                    "name": "two-schedules",
                    "to": REPORTER_ID,
                    "subject": "x",
                    "content": "y",
                    "interval_seconds": 1,
                    "cron": "* * * * *",
                },
                {
                    "name": "good",
                    "to": REPORTER_ID,
                    "subject": "好",
                    "content": "正常条目。",
                    "interval_seconds": 0.02,
                },
            ],
        )
        scheduler = _scheduler(bus, tmp_path)
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))

        await scheduler.initialize()
        await _wait_for(lambda: len(sent) == 1)

        assert [e.name for e in scheduler._entries] == ["good"]
        mail: Mail = sent[0].payload
        assert mail.subject == "好"
        await scheduler.shutdown()

    async def test_unknown_target_skipped(self, bus: EventBus, tmp_path: Path) -> None:
        _write_cron_yaml(
            tmp_path,
            [
                {
                    "name": "ghost-task",
                    "to": "ghost-agent",
                    "subject": "x",
                    "content": "给不存在的成员。",
                    "interval_seconds": 0.02,
                }
            ],
        )
        scheduler = _scheduler(bus, tmp_path, agent_ids=[REPORTER_ID])
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))

        await scheduler.initialize()
        await asyncio.sleep(0.1)

        assert sent == []
        await scheduler.shutdown()

    async def test_missing_cron_yaml_creates_no_implicit_tasks(self, bus: EventBus, tmp_path: Path) -> None:
        _make_human(tmp_path, "alice")
        scheduler = _scheduler(bus, tmp_path)
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))

        await scheduler.initialize()
        await asyncio.sleep(0)

        assert scheduler._entries == []
        assert scheduler._tasks == []
        assert sent == []
        await scheduler.shutdown()

    async def test_non_list_cron_yaml_rejected(self, bus: EventBus, tmp_path: Path) -> None:
        (tmp_path / "cron.yaml").write_text("name: not-a-list\n", encoding="utf-8")
        scheduler = _scheduler(bus, tmp_path)

        await scheduler.initialize()

        assert [e.name for e in scheduler._entries] == []
        await scheduler.shutdown()


class TestRunLoop:
    async def test_failed_tick_does_not_kill_loop(
        self, bus: EventBus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_human(tmp_path, "alice")
        _write_cron_yaml(
            tmp_path,
            [{"name": "retry", "to": REPORTER_ID, "subject": "x", "content": "y", "interval_seconds": 0.02}],
        )
        scheduler = _scheduler(bus, tmp_path)
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))

        calls = 0
        real_tick = scheduler.tick

        async def flaky_tick(entry: CronEntry) -> Mail | None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("boom")
            return await real_tick(entry)

        monkeypatch.setattr(scheduler, "tick", flaky_tick)

        await scheduler.initialize()
        await _wait_for(lambda: len(sent) == 1)

        assert calls >= 2  # the loop survived the first tick's failure
        await scheduler.shutdown()

    async def test_broken_entry_does_not_starve_other_entry(
        self, bus: EventBus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_cron_yaml(
            tmp_path,
            [
                {"name": name, "to": REPORTER_ID, "subject": name,
                 "content": "publish", "interval_seconds": 0.02}
                for name in ("broken", "working")
            ],
        )
        scheduler = _scheduler(bus, tmp_path)
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))
        broken_calls = 0
        real_tick = scheduler.tick

        async def partially_broken_tick(entry: CronEntry) -> Mail | None:
            nonlocal broken_calls
            if entry.name == "broken":
                broken_calls += 1
                raise RuntimeError("entry remains unavailable")
            return await real_tick(entry)

        monkeypatch.setattr(scheduler, "tick", partially_broken_tick)
        await scheduler.initialize()
        try:
            await _wait_for(lambda: len(sent) >= 2)
            assert broken_calls >= 2
            assert all(event.payload.subject == "working" for event in sent)
        finally:
            await scheduler.shutdown()

    async def test_shutdown_stops_the_loops(self, bus: EventBus, tmp_path: Path) -> None:
        _write_cron_yaml(
            tmp_path,
            [
                {
                    "name": "declared",
                    "to": REPORTER_ID,
                    "subject": "x",
                    "content": "y",
                    "interval_seconds": 60,
                }
            ],
        )
        scheduler = _scheduler(bus, tmp_path)
        await scheduler.initialize()
        assert len(scheduler._tasks) == 1
        await scheduler.shutdown()
        assert scheduler._tasks == []
