"""DigestScheduler — "tick is a mail" periodic scribe tasks (issue ).

Pins the tick semantics: one MAIL_NEW per interval from postmaster to the
scribe, carrying the self-contained work instructions and the human
recipient list; the tick is skipped when the scribe is unregistered or no
human is known; a failed tick never kills the run loop.
"""

import asyncio
from pathlib import Path

import pytest

from tlon.agents.digest import DigestScheduler
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
) -> DigestScheduler:
    return DigestScheduler(
        event_bus=bus,
        agent_info_provider=_AgentInfoStub(agent_ids if agent_ids is not None else [SCRIBE_ID]),
        tlon_paths=TlonPaths(home=tmp_path),
        **knobs,
    )


def _make_human(tmp_path: Path, username: str) -> None:
    """A "known human" is a persistent mailbox directory on disk."""
    (tmp_path / f"human-{username}" / "mail").mkdir(parents=True)


class TestTick:
    async def test_tick_publishes_task_mail(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        scheduler = _scheduler(bus, tmp_path)
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))

        mail = await scheduler.tick()

        assert mail is not None
        await _wait_for(lambda: len(sent) == 1)
        assert sent[0].payload is mail
        assert sent[0].source == "digest_scheduler"
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

        mail = await scheduler.tick()

        assert mail is not None
        assert "alice@tlon.internal" in mail.content
        assert "bob@tlon.internal" in mail.content

    async def test_scribe_not_registered_skips(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        scheduler = _scheduler(bus, tmp_path, agent_ids=[])
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))

        assert await scheduler.tick() is None
        await asyncio.sleep(0.05)
        assert sent == []

    async def test_no_known_humans_skips(self, bus, tmp_path):
        scheduler = _scheduler(bus, tmp_path)
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))

        assert await scheduler.tick() is None
        await asyncio.sleep(0.05)
        assert sent == []


class TestRunLoop:
    async def test_failed_tick_does_not_kill_loop(self, bus, tmp_path, monkeypatch):
        _make_human(tmp_path, "alice")
        scheduler = _scheduler(bus, tmp_path, interval_seconds=0.02)
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: sent.append(e))

        calls = 0
        real_tick = scheduler.tick

        async def flaky_tick() -> Mail | None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("boom")
            return await real_tick()

        monkeypatch.setattr(scheduler, "tick", flaky_tick)

        await scheduler.initialize()
        await _wait_for(lambda: len(sent) == 1)

        assert calls >= 2  # the loop survived the first tick's failure
        await scheduler.shutdown()

    async def test_shutdown_stops_the_loop(self, bus, tmp_path):
        scheduler = _scheduler(bus, tmp_path, interval_seconds=60)
        await scheduler.initialize()
        await scheduler.shutdown()
        assert scheduler._task is None
