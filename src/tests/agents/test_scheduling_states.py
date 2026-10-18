"""Four-state scheduling admission, durable deadlines and terminal death."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.scheduling_store import SchedulingStore
from tlon.base.config import AgentConfig, AgentInfoProvider
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.store import MailStore


class Configs(AgentInfoProvider):
    def get_config(self, agent_id: str) -> AgentConfig | None:
        return AgentConfig(id=agent_id, name=agent_id) if agent_id == "member" else None

    def get_all_ids(self) -> set[str]:
        return {"member"}


@pytest.fixture
async def registry(tmp_path: Path) -> AsyncGenerator[AgentRegistry, None]:
    bus = EventBus()
    await bus.initialize()
    paths = TlonPaths(home=tmp_path)
    home = paths.tlon_home() / "member" / "config"
    home.mkdir(parents=True)
    (home / "behavior.tlon").write_text(
        '{:version 1 :rules [{:name "mail" :on :mail-delivered :actions []}]}',
    )
    runtime = AgentRegistry(event_bus=bus, agent_info_provider=Configs(), tlon_paths=paths)
    await runtime.initialize()
    runtime.register("member")
    try:
        yield runtime
    finally:
        await runtime.shutdown()
        await bus.shutdown()


async def settle(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_sleep_blocks_mail_and_manual_boot_and_expires(registry: AgentRegistry) -> None:
    wake_at = (datetime.now(UTC) + timedelta(seconds=0.2)).isoformat()
    request = {"action": "sleep", "wake_at": wake_at, "reason": "rest"}
    await registry.prepare_scheduling_request("member", request, "session")
    await registry.apply_scheduling_request("member", request)
    status = await registry.get_agent_status("member")
    assert status is not None and status["state"] == "sleeping" and status["wake_at"] == wake_at
    assert await registry._start_agent("member") is None
    await registry._on_mail_delivered(
        Event(type=EventType.MAIL_DELIVERED, payload={"recipient": "member"}, source="test")
    )
    assert "member" not in registry._waking
    await settle(lambda: registry.can_schedule("member"))
    status = await registry.get_agent_status("member")
    assert status is not None and status["state"] == "waiting" and status["wake_at"] is None


async def test_deadline_with_unread_mail_requests_wake(registry: AgentRegistry) -> None:
    database = registry._paths.mail_database_file()
    store = MailStore(database)
    store.register_member("member")
    mailbox = Mailbox(database, "member")
    mail = Mail.create(
        from_address="sender@tlon.internal", to=["member@tlon.internal"], content="hi"
    )
    await mailbox.append_inbox(mail)
    starts: list[Event] = []
    registry.event_bus.subscribe(EventType.AGENT_START_REQUEST, lambda event: starts.append(event))
    registry._start_agent_guarded = AsyncMock(return_value=None)
    wake_at = (datetime.now(UTC) + timedelta(seconds=0.1)).isoformat()
    request = {"action": "sleep", "wake_at": wake_at}
    await registry.prepare_scheduling_request("member", request, "s")
    await registry.apply_scheduling_request("member", request)
    await settle(lambda: len(starts) > 0)
    assert starts[0].payload.agent_id == "member"
    assert await mailbox.unread() == [mail]


async def test_sleep_deadline_survives_runtime_restart(registry: AgentRegistry) -> None:
    wake_at = (datetime.now(UTC) + timedelta(seconds=0.2)).isoformat()
    request = {"action": "sleep", "wake_at": wake_at}
    await registry.prepare_scheduling_request("member", request, "s")
    await registry.shutdown()
    await registry.initialize()
    assert not registry.can_schedule("member")
    await settle(lambda: registry.can_schedule("member"))
    assert SchedulingStore(registry._paths.mail_database_file()).load() == {}


async def test_exit_intent_recovers_as_mailbox_retirement(registry: AgentRegistry) -> None:
    await registry.prepare_scheduling_request("member", {"action": "exit"}, "s")
    await registry.shutdown()
    await registry.initialize()
    status = await registry.get_agent_status("member")
    assert status is not None and status["state"] == "dead"
    assert MailStore(registry._paths.mail_database_file()).is_retired("member")
    assert await registry._start_agent("member") is None
    assert SchedulingStore(registry._paths.mail_database_file()).load() == {}


async def test_self_exit_is_background_and_does_not_await_itself(registry: AgentRegistry) -> None:
    await registry.prepare_scheduling_request("member", {"action": "exit"}, "s")
    async with asyncio.timeout(1):
        await registry.apply_scheduling_request("member", {"action": "exit"})
        await settle(lambda: "member" in registry._dead)
    status = await registry.get_agent_status("member")
    assert status is not None and status["state"] == "dead"


async def test_pending_stop_prevents_new_work(registry: AgentRegistry) -> None:
    registry._pending_stops["member"] = "operator"
    assert not registry.can_schedule("member")
    assert await registry._start_agent("member") is None


async def test_sleep_waits_for_background_work_before_shutdown(registry: AgentRegistry) -> None:
    from types import SimpleNamespace

    shutdown = AsyncMock()
    agent = SimpleNamespace(active_session_count=1, shutdown=shutdown, get_state=lambda: "active")
    registry._agents["member"] = agent
    wake_at = (datetime.now(UTC) + timedelta(seconds=10)).isoformat()
    request = {"action": "sleep", "wake_at": wake_at}
    await registry.prepare_scheduling_request("member", request, "s")
    status = await registry.get_agent_status("member")
    assert status is not None and status["state"] == "active"
    await registry.apply_scheduling_request("member", request)
    await asyncio.sleep(0.06)
    shutdown.assert_not_awaited()
    agent.active_session_count = 0
    await settle(lambda: shutdown.await_count == 1)
    status = await registry.get_agent_status("member")
    assert status is not None and status["state"] == "sleeping"


async def test_admin_termination_waits_for_background_work(registry: AgentRegistry) -> None:
    from types import SimpleNamespace

    agent = SimpleNamespace(
        active_session_count=1, shutdown=AsyncMock(), get_state=lambda: "active"
    )
    registry._agents["member"] = agent
    assert not await registry.stop_member("member", "operator", when_idle=True)
    assert not registry.can_schedule("member")
    assert not MailStore(registry._paths.mail_database_file()).is_retired("member")
    agent.shutdown.assert_not_awaited()
    agent.active_session_count = 0
    await settle(lambda: "member" in registry._dead)
    agent.shutdown.assert_awaited_once()


async def test_retirement_failure_does_not_claim_death(
    registry: AgentRegistry, monkeypatch
) -> None:
    def fail_retire(self: MailStore, member_id: str) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(MailStore, "retire", fail_retire)
    with pytest.raises(OSError, match="disk unavailable"):
        await registry.stop_member("member", "operator", when_idle=True)
    assert "member" not in registry._dead
    assert registry.can_schedule("member")
    status = await registry.get_agent_status("member")
    assert status is not None and status["state"] == "waiting"
