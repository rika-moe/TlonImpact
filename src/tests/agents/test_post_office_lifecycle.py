"""Retirement and wake recovery are independent from notification delivery."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.lifecycle import StartRequest
from tlon.base.config import AgentConfig, AgentInfoProvider
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.store import MailStore


class Provider(AgentInfoProvider):
    def get_config(self, agent_id: str) -> AgentConfig | None:
        return (
            AgentConfig(id="worker", name="Worker", idle_timeout_seconds=1)
            if agent_id == "worker"
            else None
        )

    def get_all_ids(self) -> set[str]:
        return {"worker"}


def registry_at(tmp_path: Path) -> AgentRegistry:
    paths = TlonPaths(home=tmp_path)
    behavior = paths.agent_behavior_file("worker")
    behavior.parent.mkdir(parents=True)
    behavior.write_text('{:version 1 :rules [{:name "mail" :on :mail-delivered :actions []}]}')
    bus = EventBus()
    bus.publish = AsyncMock()
    registry = AgentRegistry(event_bus=bus, agent_info_provider=Provider(), tlon_paths=paths)
    registry.register("worker")
    return registry


def member_with_sessions(count: int) -> MagicMock:
    member = MagicMock()
    member.active_session_count = count
    member.idle_seconds = 10
    member.shutdown = AsyncMock()
    return member


async def test_direct_stop_retires_sleeping_member_once(tmp_path: Path) -> None:
    registry = registry_at(tmp_path)
    assert await registry.stop_member("worker", "operator")
    assert await registry.stop_member("worker", "operator")
    assert MailStore(registry._paths.mail_database_file()).is_retired("worker")
    events = [call.args[0] for call in registry.event_bus.publish.await_args_list]
    assert len([e for e in events if e.type == EventType.AGENT_STOPPED]) == 1


async def test_idle_drain_sleeps_without_retiring(tmp_path: Path) -> None:
    registry = registry_at(tmp_path)
    member = member_with_sessions(0)
    registry._agents["worker"] = member
    await registry._reaper.drain("worker", "idle")
    assert "worker" not in registry._agents
    assert not MailStore(registry._paths.mail_database_file()).is_retired("worker")
    member.shutdown.assert_awaited_once()


async def test_pending_stop_retires_after_idle_drain_wins(tmp_path: Path) -> None:
    registry = registry_at(tmp_path)
    registry._PENDING_STOP_POLL_SECONDS = 0.001
    member = member_with_sessions(1)
    registry._agents["worker"] = member
    assert not await registry.stop_member("worker", "operator", when_idle=True)
    watcher = registry._pending_stop_tasks["worker"]
    member.active_session_count = 0
    await registry._reaper.drain("worker", "idle")
    await asyncio.wait_for(watcher, 1)
    assert MailStore(registry._paths.mail_database_file()).is_retired("worker")


async def test_unread_rescue_wakes_without_delivery_notification(tmp_path: Path) -> None:
    registry = registry_at(tmp_path)
    mail = Mail.create(
        from_address="source@tlon.internal", to=["worker@tlon.internal"], content="work"
    )
    await Mailbox(registry._paths.mail_database_file(), "worker").append_inbox(mail)
    await registry._rescue_sleeping_mail()
    events = [call.args[0] for call in registry.event_bus.publish.await_args_list]
    starts = [e for e in events if e.type == EventType.AGENT_START_REQUEST]
    assert len(starts) == 1
    assert starts[0].payload.agent_id == "worker"
    assert starts[0].payload.reason == "wake"
    assert not any(e.type == EventType.MAIL_DELIVERED for e in events)


async def test_unread_rescue_does_not_wake_retired_member(tmp_path: Path) -> None:
    registry = registry_at(tmp_path)
    mail = Mail.create(
        from_address="source@tlon.internal", to=["worker@tlon.internal"], content="work"
    )
    await Mailbox(registry._paths.mail_database_file(), "worker").append_inbox(mail)
    MailStore(registry._paths.mail_database_file()).retire("worker")
    await registry._rescue_sleeping_mail()
    registry.event_bus.publish.assert_not_awaited()


async def test_shutdown_cancels_inflight_start_without_retiring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = registry_at(tmp_path)
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_start(request: StartRequest, event: Event) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        registry._agents["worker"] = member_with_sessions(0)

    monkeypatch.setattr(registry, "_fulfill_start_request", slow_start)
    await registry._on_start_request(
        Event(
            type=EventType.AGENT_START_REQUEST,
            payload=StartRequest("worker", "wake"),
            source="test",
        )
    )
    await asyncio.wait_for(entered.wait(), 1)
    await registry.shutdown()
    assert cancelled.is_set()
    assert not registry._lifecycle_tasks
    assert not registry._agents
    assert not MailStore(registry._paths.mail_database_file()).is_retired("worker")


async def test_shelved_unread_rescue_respects_member_cadence(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    import tlon.agents.agent_registry as registry_module

    registry = registry_at(tmp_path)
    config = registry._agent_info.get_config("worker")
    assert config is not None
    config.mail_rescan_interval_seconds = 120
    monkeypatch.setattr(registry._agent_info, "get_config", lambda _: config)
    clock = [100.0]
    monkeypatch.setattr(registry_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    mail = Mail.create(
        from_address="source@tlon.internal", to=["worker@tlon.internal"], content="shelved"
    )
    await Mailbox(registry._paths.mail_database_file(), "worker").append_inbox(mail)
    registry._agents["worker"] = member_with_sessions(0)
    await registry._reaper.drain("worker", "idle")
    registry.event_bus.publish.reset_mock()

    for now in (101.0, 130.0, 160.0, 219.0):
        clock[0] = now
        await registry._rescue_sleeping_mail()
    registry.event_bus.publish.assert_not_awaited()
    clock[0] = 220.0
    await registry._rescue_sleeping_mail()
    assert registry.event_bus.publish.await_args.args[0].type is EventType.AGENT_START_REQUEST
    # If notification/start fails, the following recovery poll must still retry.
    registry.event_bus.publish.reset_mock()
    clock[0] = 250.0
    await registry._rescue_sleeping_mail()
    registry.event_bus.publish.assert_awaited_once()


async def test_new_delivery_bypasses_shelved_mail_cooldown(tmp_path: Path) -> None:
    registry = registry_at(tmp_path)
    registry._agents["worker"] = member_with_sessions(0)
    await registry._reaper.drain("worker", "idle")
    registry.event_bus.publish.reset_mock()
    await registry._on_mail_delivered(
        Event(type=EventType.MAIL_DELIVERED, payload={"recipient": "worker"}, source="post_office")
    )
    registry.event_bus.publish.assert_awaited_once()
    assert registry.event_bus.publish.await_args.args[0].payload.reason == "wake"
