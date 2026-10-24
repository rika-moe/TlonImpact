"""IdleReaper — idle fact detection and drain race-window re-checks.

Pins the scale-to-zero contract: AGENT_IDLE facts are published only for
lazy members past their timeout with no live work, and ``drain``
re-validates under the per-agent wake lock right before shutdown — a
run (or resumed activity) that lands in the race window between the
stale fact and the stop request keeps the member alive, and a shutdown
failure still leaves the member out of the run table so a later start
assembles a fresh activation.
"""

import asyncio
import contextlib
import time
from collections.abc import Callable
from typing import cast

import pytest

from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.reaper import IdleReaper
from tlon.base.config import AgentConfig
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus


class _FakeAgent:
    """Duck-typed MemberRuntime: run/idle facts plus a shutdown hook."""

    def __init__(
        self,
        is_busy: bool = False,
        idle_seconds: float = 9999.0,
        shutdown_error: Exception | None = None,
    ) -> None:
        self.is_busy = is_busy
        self.idle_seconds = idle_seconds
        self.shutdown_calls = 0
        self._shutdown_error = shutdown_error

    async def shutdown(self) -> None:
        self.shutdown_calls += 1
        if self._shutdown_error is not None:
            raise self._shutdown_error


class _StaticAgentInfo:
    def __init__(self, configs: dict[str, AgentConfig]) -> None:
        self._configs = configs

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)


class _FakeRegistry:
    """The slice of AgentRegistry the reaper touches: run table, configs,
    lazy classification, wake locks, and the event bus."""

    def __init__(
        self,
        bus: EventBus,
        configs: dict[str, AgentConfig],
        lazy: set[str],
    ) -> None:
        self._agents: dict[str, _FakeAgent] = {}
        self._agent_info = _StaticAgentInfo(configs)
        self._lazy = lazy
        self._wake_locks: dict[str, asyncio.Lock] = {}
        self.event_bus = bus

    def defer_mail_rescue(self, agent_id: str) -> None:
        """The fake has no rescue loop; verify the same member still exists."""
        assert self._agent_info.get_config(agent_id) is not None

    def is_lazy(self, agent_id: str) -> bool:
        return agent_id in self._lazy


def _config(agent_id: str, idle_timeout_seconds: int = 600) -> AgentConfig:
    return AgentConfig(id=agent_id, name=agent_id, idle_timeout_seconds=idle_timeout_seconds)


async def _make_reaper(
    configs: dict[str, AgentConfig],
    lazy: set[str],
) -> tuple[IdleReaper, _FakeRegistry, EventBus, list[Event], list[Event]]:
    """Reaper on a real bus plus capture lists for AGENT_IDLE / AGENT_SLEEPING."""
    bus = EventBus()
    await bus.initialize()
    idle_events: list[Event] = []
    sleeping_events: list[Event] = []
    bus.subscribe(EventType.AGENT_IDLE, lambda event: idle_events.append(event))
    bus.subscribe(EventType.AGENT_WAITING, lambda event: sleeping_events.append(event))
    registry = _FakeRegistry(bus, configs, lazy)
    return IdleReaper(cast(AgentRegistry, registry)), registry, bus, idle_events, sleeping_events


async def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


async def test_idle_fact_published_for_idle_lazy_member() -> None:
    reaper, registry, bus, idle_events, _ = await _make_reaper({"a": _config("a")}, {"a"})
    registry._agents["a"] = _FakeAgent(idle_seconds=601.7)
    try:
        await reaper._report_if_idle("a")

        assert await _wait_for(lambda: len(idle_events) == 1)
        assert idle_events[0].payload == {"agent_id": "a", "idle_seconds": 601}
        assert idle_events[0].source == "agent_registry"
    finally:
        await bus.shutdown()


async def test_idle_fact_skipped_with_live_run() -> None:
    reaper, registry, bus, idle_events, _ = await _make_reaper({"a": _config("a")}, {"a"})
    registry._agents["a"] = _FakeAgent(is_busy=True, idle_seconds=9999.0)
    try:
        await reaper._report_if_idle("a")
        await asyncio.sleep(0.05)  # give any misfired publish a chance to dispatch
        assert idle_events == []
    finally:
        await bus.shutdown()


async def test_idle_fact_skipped_below_timeout() -> None:
    reaper, registry, bus, idle_events, _ = await _make_reaper(
        {"a": _config("a", idle_timeout_seconds=600)}, {"a"}
    )
    registry._agents["a"] = _FakeAgent(idle_seconds=599.9)
    try:
        await reaper._report_if_idle("a")
        await asyncio.sleep(0.05)  # give any misfired publish a chance to dispatch
        assert idle_events == []
    finally:
        await bus.shutdown()


async def test_idle_fact_skipped_for_non_lazy_member() -> None:
    """A boot-set member idling past the timeout is not a scale-to-zero fact."""
    reaper, registry, bus, idle_events, _ = await _make_reaper({"a": _config("a")}, set())
    registry._agents["a"] = _FakeAgent()
    try:
        await reaper._report_if_idle("a")
        await asyncio.sleep(0.05)  # give any misfired publish a chance to dispatch
        assert idle_events == []
    finally:
        await bus.shutdown()


async def test_idle_fact_skipped_without_config_or_agent() -> None:
    reaper, registry, bus, idle_events, _ = await _make_reaper(
        {"gone": _config("gone")}, {"gone", "unknown"}
    )
    try:
        await reaper._report_if_idle("gone")  # config but not in the run table
        await reaper._report_if_idle("unknown")  # no config at all
        await asyncio.sleep(0.05)  # give any misfired publish a chance to dispatch
        assert idle_events == []
    finally:
        await bus.shutdown()


async def test_drain_sleeps_idle_member() -> None:
    reaper, registry, bus, _, sleeping_events = await _make_reaper({"a": _config("a")}, {"a"})
    agent = _FakeAgent(idle_seconds=700.2)
    registry._agents["a"] = agent
    try:
        await reaper.drain("a", reason="drain")

        assert "a" not in registry._agents
        assert agent.shutdown_calls == 1
        assert await _wait_for(lambda: len(sleeping_events) == 1)
        assert sleeping_events[0].payload == {"agent_id": "a", "state": "waiting"}
    finally:
        await bus.shutdown()


async def test_drain_keeps_member_with_live_run() -> None:
    """The fact was stale on arrival: a run started before the stop
    request executed, so the pre-lock check already saves the member."""
    reaper, registry, bus, _, sleeping_events = await _make_reaper({"a": _config("a")}, {"a"})
    agent = _FakeAgent(is_busy=True, idle_seconds=9999.0)
    registry._agents["a"] = agent
    try:
        await reaper.drain("a", reason="drain")

        assert "a" in registry._agents
        assert agent.shutdown_calls == 0
        await asyncio.sleep(0.05)  # give any misfired publish a chance to dispatch
        assert sleeping_events == []
    finally:
        await bus.shutdown()


async def test_drain_recheck_under_lock_saves_run_started_in_window() -> None:
    """The race window the wake lock exists for: the member passes the
    pre-lock check idle, then a run starts while drain waits on the
    lock (a concurrent wake holds it). The under-lock re-check must keep
    the member alive."""
    reaper, registry, bus, _, sleeping_events = await _make_reaper({"a": _config("a")}, {"a"})
    agent = _FakeAgent(is_busy=False, idle_seconds=9999.0)
    registry._agents["a"] = agent
    lock = asyncio.Lock()
    registry._wake_locks["a"] = lock
    await lock.acquire()
    try:
        task = asyncio.create_task(reaper.drain("a", reason="drain"))
        await asyncio.sleep(0)  # let drain pass the pre-lock check and block on the lock
        agent.is_busy = True  # run starts inside the race window
        lock.release()
        await task

        assert "a" in registry._agents
        assert agent.shutdown_calls == 0
        await asyncio.sleep(0.05)  # give any misfired publish a chance to dispatch
        assert sleeping_events == []
    finally:
        if lock.locked():
            lock.release()
        await bus.shutdown()


async def test_drain_recheck_under_lock_saves_resumed_activity() -> None:
    """Same window, no run: background activity resumed (idle clock
    reset below the timeout) while drain waited on the lock."""
    reaper, registry, bus, _, sleeping_events = await _make_reaper(
        {"a": _config("a", idle_timeout_seconds=600)}, {"a"}
    )
    agent = _FakeAgent(is_busy=False, idle_seconds=9999.0)
    registry._agents["a"] = agent
    lock = asyncio.Lock()
    registry._wake_locks["a"] = lock
    await lock.acquire()
    try:
        task = asyncio.create_task(reaper.drain("a", reason="drain"))
        await asyncio.sleep(0)
        agent.idle_seconds = 3.0  # activity resumed inside the race window
        lock.release()
        await task

        assert "a" in registry._agents
        assert agent.shutdown_calls == 0
        await asyncio.sleep(0.05)  # give any misfired publish a chance to dispatch
        assert sleeping_events == []
    finally:
        if lock.locked():
            lock.release()
        await bus.shutdown()


async def test_drain_member_gone_under_lock() -> None:
    """Someone else drained the member while this drain waited on the lock:
    the under-lock re-read finds no agent and returns without an event."""
    reaper, registry, bus, _, sleeping_events = await _make_reaper({"a": _config("a")}, {"a"})
    registry._agents["a"] = _FakeAgent()
    lock = asyncio.Lock()
    registry._wake_locks["a"] = lock
    await lock.acquire()
    try:
        task = asyncio.create_task(reaper.drain("a", reason="drain"))
        await asyncio.sleep(0)
        registry._agents.pop("a")
        lock.release()
        await task  # must not raise

        await asyncio.sleep(0.05)  # give any misfired publish a chance to dispatch
        assert sleeping_events == []
    finally:
        if lock.locked():
            lock.release()
        await bus.shutdown()


async def test_drain_shutdown_failure_leaves_member_out_of_run_table() -> None:
    """A failed shutdown is logged, never raised; the member is already out
    of the run table so a later start assembles a fresh activation, and no
    AGENT_SLEEPING fact claims a sleep that did not happen."""
    reaper, registry, bus, _, sleeping_events = await _make_reaper({"a": _config("a")}, {"a"})
    agent = _FakeAgent(shutdown_error=RuntimeError("sandbox stuck"))
    registry._agents["a"] = agent
    try:
        await reaper.drain("a", reason="drain")  # must not raise

        assert "a" not in registry._agents
        assert agent.shutdown_calls == 1
        await asyncio.sleep(0.05)  # give any misfired publish a chance to dispatch
        assert sleeping_events == []
    finally:
        await bus.shutdown()


async def test_drain_noop_for_non_lazy_or_unknown_member() -> None:
    reaper, registry, bus, _, sleeping_events = await _make_reaper({"a": _config("a")}, set())
    agent = _FakeAgent()
    registry._agents["a"] = agent
    try:
        await reaper.drain("a", reason="drain")  # boot-set member: never drained
        await reaper.drain("unknown", reason="drain")  # no config

        assert "a" in registry._agents
        assert agent.shutdown_calls == 0
        await asyncio.sleep(0.05)  # give any misfired publish a chance to dispatch
        assert sleeping_events == []
    finally:
        await bus.shutdown()


async def test_run_loop_isolates_per_agent_check_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One member's check raising does not kill the monitor loop: the next
    member is still scanned, and the loop keeps ticking."""
    monkeypatch.setattr(IdleReaper, "IDLE_CHECK_INTERVAL_SECONDS", 0.01)
    reaper, registry, bus, idle_events, _ = await _make_reaper({"good": _config("good")}, {"good"})
    registry._agents["good"] = _FakeAgent()

    async def boom(agent_id: str) -> None:
        if agent_id == "bad":
            raise RuntimeError("config backend down")
        await type(reaper)._report_if_idle(reaper, agent_id)

    registry._agents["bad"] = _FakeAgent()
    monkeypatch.setattr(reaper, "_report_if_idle", boom)
    try:
        task = asyncio.create_task(reaper.run())
        for _ in range(100):
            if idle_events:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert not task.cancelled() or idle_events  # loop survived the failure
        assert [e.payload["agent_id"] for e in idle_events] == ["good"]
    finally:
        await bus.shutdown()
