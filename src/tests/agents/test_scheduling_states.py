"""Four-state scheduling admission, durable deadlines and terminal death."""

import asyncio
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.program_helpers import write_program
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
    write_program(home / "behavior.tlon", "{:version 2 :handlers {:mail-delivered work/handle}}")
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
    await registry.prepare_scheduling_request("member", request, "run")
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
    agent = SimpleNamespace(is_busy=True, shutdown=shutdown, get_state=lambda: "active")
    registry._agents["member"] = agent
    wake_at = (datetime.now(UTC) + timedelta(seconds=10)).isoformat()
    request = {"action": "sleep", "wake_at": wake_at}
    await registry.prepare_scheduling_request("member", request, "s")
    status = await registry.get_agent_status("member")
    assert status is not None and status["state"] == "active"
    await registry.apply_scheduling_request("member", request)
    await asyncio.sleep(0.06)
    shutdown.assert_not_awaited()
    agent.is_busy = False
    await settle(lambda: shutdown.await_count == 1)
    status = await registry.get_agent_status("member")
    assert status is not None and status["state"] == "sleeping"


async def test_admin_termination_waits_for_background_work(registry: AgentRegistry) -> None:
    from types import SimpleNamespace

    agent = SimpleNamespace(is_busy=True, shutdown=AsyncMock(), get_state=lambda: "active")
    registry._agents["member"] = agent
    assert not await registry.stop_member("member", "operator", when_idle=True)
    assert not registry.can_schedule("member")
    assert not MailStore(registry._paths.mail_database_file()).is_retired("member")
    agent.shutdown.assert_not_awaited()
    agent.is_busy = False
    await settle(lambda: "member" in registry._dead and agent.shutdown.await_count == 1)
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


async def test_admin_wake_without_mail_does_not_start_work(registry: AgentRegistry) -> None:
    deadline = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert await registry.sleep_member("member", deadline)
    timer = registry._sleep_tasks["member"]
    registry._start_agent_guarded = AsyncMock()
    assert await registry.wake_member("member")
    await asyncio.sleep(0)
    assert timer.cancelled()
    assert registry.can_schedule("member")
    assert SchedulingStore(registry._paths.mail_database_file()).load() == {}
    registry._start_agent_guarded.assert_not_awaited()
    assert (await registry.get_agent_status("member"))["state"] == "waiting"


async def test_admin_wake_with_mail_uses_normal_delivery(registry: AgentRegistry) -> None:
    database = registry._paths.mail_database_file()
    MailStore(database).register_member("member")
    mailbox = Mailbox(database, "member")
    mail = Mail.create(
        from_address="sender@tlon.internal", to=["member@tlon.internal"], content="hi"
    )
    await mailbox.append_inbox(mail)
    registry._start_agent_guarded = AsyncMock(return_value=None)
    starts: list[Event] = []
    registry.event_bus.subscribe(EventType.AGENT_START_REQUEST, lambda event: starts.append(event))
    await registry.sleep_member("member", (datetime.now(UTC) + timedelta(hours=1)).isoformat())
    await registry.wake_member("member")
    await settle(lambda: bool(starts))
    assert len(starts) == 1
    assert await mailbox.unread() == [mail]


async def test_admin_sleep_adjustment_rejects_stale_timer_even_same_deadline(
    registry: AgentRegistry,
) -> None:
    deadline = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    await registry.sleep_member("member", deadline)
    old = registry._scheduling_intents["member"]
    await registry.sleep_member("member", deadline)
    new = registry._scheduling_intents["member"]
    assert old != new
    # Simulate an obsolete timer already past its sleep, after a same-deadline replacement.
    await registry._wake_at_deadline("member", datetime.now(UTC).isoformat(), old)
    assert registry._scheduling_intents["member"] == new
    assert not registry.can_schedule("member")
    await registry.wake_member("member")
    await registry._wake_at_deadline("member", datetime.now(UTC).isoformat(), new)
    assert registry.can_schedule("member")


@pytest.mark.parametrize("action", ["sleep", "wait"])
async def test_admin_drain_allows_run_to_finish_and_commit_self_intent(
    registry: AgentRegistry, action: str
) -> None:
    from types import SimpleNamespace

    agent = SimpleNamespace(is_busy=True, shutdown=AsyncMock(), get_state=lambda: "active")
    registry._agents["member"] = agent
    deadline = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    completed = (
        await registry.sleep_member("member", deadline)
        if action == "sleep"
        else await registry.wait_member("member")
    )
    assert not completed and not registry.can_schedule("member")
    status = await registry.get_agent_status("member")
    assert status is not None and status["state"] == "active"
    assert status["scheduling_pending"] == action
    # Committing a concurrent self sleep must neither deadlock nor replace admin intent.
    async with asyncio.timeout(1):
        await registry.prepare_scheduling_request(
            "member", {"action": "sleep", "wake_at": deadline}, "self-run"
        )
    assert registry._scheduling_intents["member"][0] == action
    assert registry._scheduling_intents["member"][2].startswith("admin:")
    agent.shutdown.assert_not_awaited()
    agent.is_busy = False
    await settle(lambda: agent.shutdown.await_count == 1)
    await settle(lambda: registry.can_schedule("member") == (action == "wait"))
    status = await registry.get_agent_status("member")
    assert status is not None and status["state"] == ("waiting" if action == "wait" else "sleeping")


async def test_self_exit_overrides_admin_drain(registry: AgentRegistry) -> None:
    from types import SimpleNamespace

    agent = SimpleNamespace(is_busy=True, shutdown=AsyncMock(), get_state=lambda: "active")
    registry._agents["member"] = agent
    assert not await registry.wait_member("member")
    await registry.prepare_scheduling_request("member", {"action": "exit"}, "self-exit")
    assert registry._scheduling_intents["member"][0] == "exit"
    agent.is_busy = False
    await registry.apply_scheduling_request("member", {"action": "exit"})
    await settle(lambda: "member" in registry._dead and agent.shutdown.await_count == 1)
    agent.shutdown.assert_awaited_once()


@pytest.mark.parametrize("operation", ["wake", "wait", "sleep"])
@pytest.mark.parametrize("terminal", ["retired", "pending", "self_exit"])
async def test_no_admin_operation_revives_terminal_member(
    registry: AgentRegistry, operation: str, terminal: str
) -> None:
    from tlon.lifecycle.ports import StopRejectedError

    if terminal == "retired":
        await registry.stop_member("member", "operator")
    elif terminal == "pending":
        registry._pending_stops["member"] = "operator"
    else:
        await registry.prepare_scheduling_request("member", {"action": "exit"}, "run")
    with pytest.raises(StopRejectedError, match="dead or permanently exiting"):
        if operation == "sleep":
            await registry.sleep_member(
                "member", (datetime.now(UTC) + timedelta(hours=1)).isoformat()
            )
        elif operation == "wait":
            await registry.wait_member("member")
        else:
            await registry.wake_member("member")
    assert not registry.can_schedule("member")


@pytest.mark.parametrize("operation", ["wake", "wait", "sleep"])
async def test_admin_operation_unknown_member(registry: AgentRegistry, operation: str) -> None:
    from tlon.lifecycle.ports import StopRejectedError

    with pytest.raises(StopRejectedError) as error:
        if operation == "sleep":
            await registry.sleep_member(
                "missing", (datetime.now(UTC) + timedelta(hours=1)).isoformat()
            )
        elif operation == "wait":
            await registry.wait_member("missing")
        else:
            await registry.wake_member("missing")
    assert error.value.reason == "unknown_agent"


@pytest.mark.parametrize(
    "deadline", ["2020-01-01T00:00:00+00:00", "2099-01-01T00:00:00", "invalid"]
)
async def test_admin_sleep_requires_future_timezone(registry: AgentRegistry, deadline: str) -> None:
    with pytest.raises(ValueError):
        await registry.sleep_member("member", deadline)
    assert registry.can_schedule("member")


async def test_admin_sleep_normalizes_timezone_and_survives_restart(
    registry: AgentRegistry,
) -> None:
    deadline = datetime.now(UTC) + timedelta(hours=1)
    from datetime import timezone

    local = deadline.astimezone(timezone(timedelta(hours=8))).isoformat()
    await registry.sleep_member("member", local)
    assert registry._scheduling_intents["member"][1] == deadline.isoformat()
    await registry.shutdown()
    await registry.initialize()
    assert (await registry.get_agent_status("member"))["wake_at"] == deadline.isoformat()
    await registry.wake_member("member")
    assert registry.can_schedule("member")


async def test_wait_drain_recovers_without_stuck_gate(registry: AgentRegistry) -> None:
    SchedulingStore(registry._paths.mail_database_file()).put(
        "member", "wait", None, "admin:restart"
    )
    await registry.shutdown()
    await registry.initialize()
    assert registry.can_schedule("member")
    assert SchedulingStore(registry._paths.mail_database_file()).load() == {}


async def test_wait_checks_mail_queued_during_drain(registry: AgentRegistry) -> None:
    from types import SimpleNamespace

    database = registry._paths.mail_database_file()
    MailStore(database).register_member("member")
    agent = SimpleNamespace(is_busy=True, shutdown=AsyncMock(), get_state=lambda: "active")
    registry._agents["member"] = agent
    registry._start_agent_guarded = AsyncMock(return_value=None)
    starts: list[Event] = []
    registry.event_bus.subscribe(EventType.AGENT_START_REQUEST, lambda event: starts.append(event))
    assert not await registry.wait_member("member")
    mailbox = Mailbox(database, "member")
    mail = Mail.create(
        from_address="sender@tlon.internal", to=["member@tlon.internal"], content="new"
    )
    await mailbox.append_inbox(mail)
    await registry._on_mail_delivered(
        Event(type=EventType.MAIL_DELIVERED, payload={"recipient": "member"}, source="test")
    )
    assert starts == []
    agent.is_busy = False
    await settle(lambda: bool(starts))
    assert len(starts) == 1
    assert await mailbox.unread() == [mail]


async def test_conflicting_admin_transitions_are_rejected(registry: AgentRegistry) -> None:
    from types import SimpleNamespace

    from tlon.lifecycle.ports import StopRejectedError

    with pytest.raises(StopRejectedError):
        await registry.wake_member("member")
    deadline = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    await registry.sleep_member("member", deadline)
    with pytest.raises(StopRejectedError):
        await registry.wait_member("member")
    await registry.wake_member("member")
    agent = SimpleNamespace(is_busy=True, shutdown=AsyncMock(), get_state=lambda: "active")
    registry._agents["member"] = agent
    await registry.sleep_member("member", deadline)
    for action in (
        registry.wake_member("member"),
        registry.wait_member("member"),
        registry.sleep_member("member", deadline),
    ):
        with pytest.raises(StopRejectedError):
            await action
    agent.is_busy = False


async def test_unfinished_admin_sleep_is_not_committed_across_restart(
    registry: AgentRegistry,
) -> None:
    from types import SimpleNamespace

    agent = SimpleNamespace(is_busy=True, shutdown=AsyncMock(), get_state=lambda: "active")
    registry._agents["member"] = agent
    deadline = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert not await registry.sleep_member("member", deadline)
    assert registry._scheduling_intents["member"][2].startswith("admin:pending:")
    await registry.shutdown()
    await registry.initialize()
    assert registry.can_schedule("member")
    assert SchedulingStore(registry._paths.mail_database_file()).load() == {}
    assert (await registry.get_agent_status("member"))["state"] == "waiting"


async def test_program_sleep_replay_after_wake_does_not_sleep_again(
    registry: AgentRegistry,
) -> None:
    request = {"action": "sleep", "wake_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
    await registry.apply_program_scheduling("member", "entry:one", request)
    await settle(lambda: "member" in registry._sleep_tasks)
    await registry.wake_member("member")
    await registry.shutdown()
    await registry.initialize()
    await registry.apply_program_scheduling("member", "entry:one", request)
    assert registry.can_schedule("member")
    assert registry._scheduling_store.load() == {}


async def test_program_replay_cannot_replace_new_admin_sleep(registry: AgentRegistry) -> None:
    request = {"action": "sleep", "wake_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
    await registry.apply_program_scheduling("member", "entry:one", request)
    await settle(lambda: "member" in registry._sleep_tasks)
    admin_deadline = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    await registry.sleep_member("member", admin_deadline)
    before = registry._scheduling_store.load()
    await registry.apply_program_scheduling("member", "entry:one", request)
    assert registry._scheduling_store.load() == before


async def test_program_request_blocked_by_admin_is_consumed(registry: AgentRegistry) -> None:
    request = {"action": "sleep", "wake_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
    await registry.sleep_member("member", request["wake_at"])
    await registry.apply_program_scheduling("member", "entry:blocked", request)
    await registry.wake_member("member")
    await registry.apply_program_scheduling("member", "entry:blocked", request)
    assert registry.can_schedule("member")


async def test_program_replay_after_exit_cannot_resurrect_member(registry: AgentRegistry) -> None:
    request = {"action": "sleep", "wake_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}
    await registry.apply_program_scheduling("member", "entry:one", request)
    await registry.stop_member("member", stopped_by="admin")
    await registry.apply_program_scheduling("member", "entry:one", request)
    assert "member" in registry._dead
    assert registry._scheduling_store.load() == {}


@pytest.mark.parametrize(
    "actions,reflecting,expected",
    [(0, False, False), (1, False, True), (0, True, True), (2, True, True)],
)
def test_member_busy_covers_handlers_and_maintenance(
    actions: int, reflecting: bool, expected: bool
) -> None:
    from types import SimpleNamespace

    from tlon.agents.member_runtime import MemberRuntime

    member = object.__new__(MemberRuntime)
    member._active_actions = actions
    member._reflection = SimpleNamespace(is_running=reflecting)  # type: ignore[assignment]
    assert member.is_busy is expected
