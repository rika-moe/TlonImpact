"""CronScheduler — "tick is a mail" periodic task mails (issue ).

Pins the generalized tick semantics: one MAIL_NEW per entry per schedule
from postmaster to the entry's target member; the built-in digest entry
behaves exactly as 's DigestScheduler did (self-contained work
instructions + human recipient list, skipped when the scribe is
unregistered or no human is known); declared entries load from cron.yaml
(interval or cron schedule), invalid entries are isolated, unknown
targets are skipped, and a failed tick never kills an entry's loop.
"""

import asyncio
from pathlib import Path

import pytest
import yaml

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

SCRIBE_ID = "ji-shuheng"


class _AgentInfoStub(AgentInfoProvider):
    """Read-only member enumeration for the scheduler."""

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


def _scheduler(
    bus: EventBus,
    tmp_path: Path,
    agent_ids: list[str] | None = None,
    **knobs,
) -> CronScheduler:
    return CronScheduler(
        event_bus=bus,
        agent_info_provider=_AgentInfoStub(agent_ids if agent_ids is not None else [SCRIBE_ID]),
        tlon_paths=TlonPaths(home=tmp_path),
        **knobs,
    )


def _digest_entry(scheduler: CronScheduler) -> CronEntry:
    return scheduler._builtin_entries[0]


def _make_human(tmp_path: Path, username: str) -> None:
    """A "known human" is a persistent mailbox directory on disk."""
    (tmp_path / f"human-{username}" / "mail").mkdir(parents=True)


def _write_cron_yaml(tmp_path: Path, entries: object) -> None:
    (tmp_path / "cron.yaml").write_text(yaml.dump(entries), encoding="utf-8")


class TestBuiltinDigestTick:
    async def test_tick_publishes_task_mail(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        scheduler = _scheduler(bus, tmp_path)
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))

        mail = await scheduler.tick(_digest_entry(scheduler))

        assert mail is not None
        await _wait_for(lambda: len(sent) == 1)
        assert sent[0].payload is mail
        assert sent[0].source == "cron_scheduler"
        assert mail.from_address == POSTMASTER_ADDRESS
        assert mail.to == [address_for_member(SCRIBE_ID)]
        assert mail.subject is not None
        # The instructions are self-contained: tool list, coverage areas,
        # human recipients, and the send method.
        assert "alice@tlon.internal" in mail.content
        assert "getSocialHealth" in mail.content
        assert "listStagingPrototypes" in mail.content
        assert "is_complete=true" in mail.content

    async def test_tick_lists_all_known_humans(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        _make_human(tmp_path, "bob")
        scheduler = _scheduler(bus, tmp_path)

        mail = await scheduler.tick(_digest_entry(scheduler))

        assert mail is not None
        assert "alice@tlon.internal" in mail.content
        assert "bob@tlon.internal" in mail.content

    async def test_scribe_not_registered_skips(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        scheduler = _scheduler(bus, tmp_path, agent_ids=[])
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))

        assert await scheduler.tick(_digest_entry(scheduler)) is None
        await asyncio.sleep(0.05)
        assert sent == []

    async def test_no_known_humans_skips(self, bus, tmp_path):
        scheduler = _scheduler(bus, tmp_path)
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))

        assert await scheduler.tick(_digest_entry(scheduler)) is None
        await asyncio.sleep(0.05)
        assert sent == []


class TestDeclaredEntries:
    async def test_interval_entry_fires_from_cron_yaml(self, bus, tmp_path):
        _write_cron_yaml(
            tmp_path,
            [
                {
                    "name": "staging-check",
                    "to": SCRIBE_ID,
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
        assert sent[0].source == "cron_scheduler"
        assert mail.from_address == POSTMASTER_ADDRESS
        assert mail.to == [address_for_member(SCRIBE_ID)]
        assert mail.subject == "定期检查 staging 提案"
        assert "prototypes-staging" in mail.content
        await scheduler.shutdown()

    async def test_declared_entry_to_known_human(self, bus, tmp_path):
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

    async def test_cron_schedule_computes_next_fire(self, bus, tmp_path):
        _write_cron_yaml(
            tmp_path,
            [
                {
                    "name": "morning-report",
                    "to": SCRIBE_ID,
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

    async def test_invalid_entry_isolated(self, bus, tmp_path):
        _write_cron_yaml(
            tmp_path,
            [
                {"name": "missing-to", "subject": "x", "content": "y", "interval_seconds": 1},
                {
                    "name": "bad-cron",
                    "to": SCRIBE_ID,
                    "subject": "x",
                    "content": "y",
                    "cron": "not a cron",
                },
                {
                    "name": "two-schedules",
                    "to": SCRIBE_ID,
                    "subject": "x",
                    "content": "y",
                    "interval_seconds": 1,
                    "cron": "* * * * *",
                },
                {
                    "name": "good",
                    "to": SCRIBE_ID,
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

        assert [e.name for e in scheduler._entries] == ["digest", "good"]
        mail: Mail = sent[0].payload
        assert mail.subject == "好"
        await scheduler.shutdown()

    async def test_unknown_target_skipped(self, bus, tmp_path):
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
        scheduler = _scheduler(bus, tmp_path, agent_ids=[SCRIBE_ID])
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))

        await scheduler.initialize()
        await asyncio.sleep(0.1)

        assert sent == []
        await scheduler.shutdown()

    async def test_missing_cron_yaml_is_fine(self, bus, tmp_path):
        scheduler = _scheduler(bus, tmp_path)

        await scheduler.initialize()

        assert [e.name for e in scheduler._entries] == ["digest"]
        await scheduler.shutdown()

    async def test_non_list_cron_yaml_rejected(self, bus, tmp_path):
        (tmp_path / "cron.yaml").write_text("name: not-a-list\n", encoding="utf-8")
        scheduler = _scheduler(bus, tmp_path)

        await scheduler.initialize()

        assert [e.name for e in scheduler._entries] == ["digest"]
        await scheduler.shutdown()


class TestRunLoop:
    async def test_failed_tick_does_not_kill_loop(self, bus, tmp_path, monkeypatch):
        _make_human(tmp_path, "alice")
        scheduler = _scheduler(bus, tmp_path, interval_seconds=0.02)
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

    async def test_shutdown_stops_the_loops(self, bus, tmp_path):
        _write_cron_yaml(
            tmp_path,
            [
                {
                    "name": "declared",
                    "to": SCRIBE_ID,
                    "subject": "x",
                    "content": "y",
                    "interval_seconds": 60,
                }
            ],
        )
        scheduler = _scheduler(bus, tmp_path, interval_seconds=60)
        await scheduler.initialize()
        assert len(scheduler._tasks) == 2
        await scheduler.shutdown()
        assert scheduler._tasks == []
