"""Tests for the dynamic AgentRegistry (阶段 6a/阶段 D: 休眠/唤醒/机制化).

Covers:
- Boot split: initialize() is pure mechanism — no agent starts here
  (the boot policy is Adam's, covered in test_adam.py)
- Start requests: AGENT_START_REQUEST starts agents idempotently;
  wake requests publish WAKING/WOKEN and redeliver the trigger event;
  boot requests publish no wake observability; concurrent requests
  assemble exactly once
- Idle facts + drain execution: idle lazy agents are reported as
  AGENT_IDLE facts (never stopped by the monitor); AGENT_STOP_REQUEST
  drains with a condition re-check under the per-agent lock
- Docker lazy start: container not started at agent assembly, started on
  first tool execution
"""

import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.lifecycle import StartRequest, StopRequest
from tlon.base.config import AgentConfig, DockerRuntimeConfig
from tlon.base.events.event import Event, EventType
from tlon.base.paths import TlonPaths
from tlon.organization.messaging.message import Message
from tlon.organization.messaging.router import RoutedMessage
from tlon.sandbox.docker_runtime import DockerRuntime

TRACE_ID = "00000000000000000000000000000001"
SPAN_ID = "0000000000000001"


class MockAgentInfoProvider:
    """Mock AgentInfoProvider for testing."""

    def __init__(self, configs: dict[str, AgentConfig]):
        self._configs = configs

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)

    def get_all_ids(self) -> set[str]:
        return set(self._configs.keys())

    def is_proactive(self, agent_id: str) -> bool:
        config = self.get_config(agent_id)
        return config.proactive if config else False


def _make_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock()
    bus.subscribe = MagicMock(side_effect=["sub-start", "sub-stop"])
    bus.unsubscribe = MagicMock()
    return bus


def _make_running_agent(active_sessions: int = 0, idle_seconds: float = 0.0) -> MagicMock:
    """A stand-in for a running Agent in the registry's run table."""
    agent = MagicMock()
    agent.shutdown = AsyncMock()
    agent.get_state = MagicMock(return_value="active")
    agent.active_session_count = active_sessions
    agent.idle_seconds = idle_seconds
    return agent


def _routed_event(
    target_ids: list[str],
    mention_target_ids: list[str],
    content: str = "hi",
) -> Event:
    message = Message.create(
        sender_id="human",
        sender_name="用户",
        sender_type="human",
        content=content,
        mentions=mention_target_ids,
    )
    routed = RoutedMessage(
        original=message,
        target_ids=target_ids,
        routing_reason="explicit_mentions" if mention_target_ids else "proactive_broadcast",
        mention_target_ids=mention_target_ids,
    )
    return Event(
        type=EventType.MESSAGE_ROUTED,
        payload=routed,
        source="message_router",
        trace_id=TRACE_ID,
        span_id=SPAN_ID,
    )


def _request_event(request: StartRequest | StopRequest) -> Event:
    """Wrap a lifecycle request in the bus event Adam would publish."""
    event_type = (
        EventType.AGENT_START_REQUEST
        if isinstance(request, StartRequest)
        else EventType.AGENT_STOP_REQUEST
    )
    return Event(type=event_type, payload=request, source="adam")


@pytest.fixture
def temp_paths():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield TlonPaths(home=tmpdir)


def _make_registry(
    configs: dict[str, AgentConfig], temp_paths: TlonPaths
) -> tuple[AgentRegistry, MagicMock]:
    bus = _make_bus()
    registry = AgentRegistry(
        event_bus=bus,
        agent_info_provider=MockAgentInfoProvider(configs),
        message_store=MagicMock(),
        paths=temp_paths,
    )
    return registry, bus


class TestBootSplit:
    """initialize() is pure mechanism: no agent starts here (阶段 D)."""

    @pytest.mark.asyncio
    async def test_all_lazy_boots_zero_running(self, temp_paths):
        configs = {
            "a-001": AgentConfig(id="a-001", name="甲", activation="lazy"),
            "a-002": AgentConfig(id="a-002", name="乙", activation="lazy"),
        }
        registry, _bus = _make_registry(configs, temp_paths)
        registry.register_all(["a-001", "a-002"])

        with patch.object(registry, "_start_agent", new=AsyncMock()) as mock_start:
            await registry.initialize()
            mock_start.assert_not_called()

        assert registry.get_all_agents() == []
        assert registry.get_agent_status("a-001")["state"] == "sleeping"
        assert registry.get_agent_status("a-002")["state"] == "sleeping"

        await registry.shutdown()

    @pytest.mark.asyncio
    async def test_initialize_starts_nothing_even_always(self, temp_paths):
        """The boot start-set decision moved to Adam: registry initialize
        only subscribes to requests and starts the idle monitor."""
        configs = {
            "a-001": AgentConfig(id="a-001", name="甲", activation="always"),
            "a-002": AgentConfig(id="a-002", name="乙", activation="lazy"),
        }
        registry, _bus = _make_registry(configs, temp_paths)
        registry.register_all(["a-001", "a-002"])

        with patch.object(registry, "_start_agent", new=AsyncMock()) as mock_start:
            await registry.initialize()
            mock_start.assert_not_called()

        assert registry.get_all_agents() == []
        assert registry.get_agent_status("a-001")["state"] == "unknown"
        assert registry.get_agent_status("a-002")["state"] == "sleeping"

        await registry.shutdown()

    @pytest.mark.asyncio
    async def test_always_agent_not_running_is_unknown(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="always")}
        registry, _bus = _make_registry(configs, temp_paths)
        # Not registered/started — simulates a failed always-agent
        assert registry.get_agent_status("a-001")["state"] == "unknown"

    @pytest.mark.asyncio
    async def test_default_activation_is_always(self):
        config = AgentConfig(id="a-001", name="甲")
        assert config.activation == "always"
        assert config.idle_timeout_seconds == 600


class TestStartRequestExecution:
    """AGENT_START_REQUEST starts agents idempotently; wake requests
    publish WAKING/WOKEN and redeliver the trigger event."""

    @pytest.mark.asyncio
    async def test_wake_request_starts_and_redelivers(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="lazy")}
        registry, bus = _make_registry(configs, temp_paths)
        registry.register("a-001")
        await registry.initialize()

        woken = _make_running_agent()

        async def fake_start(agent_id: str):
            registry._agents[agent_id] = woken
            return woken

        trigger = _routed_event(["a-001"], ["a-001"])
        request_event = _request_event(
            StartRequest(agent_id="a-001", reason="wake", trigger_event=trigger)
        )

        with patch.object(registry, "_start_agent", side_effect=fake_start) as mock_start:
            await registry._on_start_request(request_event)
            await asyncio.sleep(0.05)

        mock_start.assert_awaited_once_with("a-001")

        published = [call.args[0] for call in bus.publish.await_args_list]
        published_types = [e.type for e in published]
        # Observability events carry the original trace carrier
        assert EventType.AGENT_WAKING in published_types
        assert EventType.AGENT_WOKEN in published_types
        woken_event = next(e for e in published if e.type == EventType.AGENT_WOKEN)
        assert woken_event.payload["agent_id"] == "a-001"
        assert "wake_duration_seconds" in woken_event.payload
        assert woken_event.trace_id == TRACE_ID
        # The trigger event is re-published verbatim (same payload
        # and trace carrier) so the new agent's handler picks it up
        assert trigger in published

        await registry.shutdown()

    @pytest.mark.asyncio
    async def test_boot_request_starts_without_wake_events(self, temp_paths):
        """Boot fulfillment keeps boot's old behavior: logs only, no
        WAKING/WOKEN observability events, no redelivery."""
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="always")}
        registry, bus = _make_registry(configs, temp_paths)
        registry.register("a-001")
        await registry.initialize()

        async def fake_start(agent_id: str):
            agent = _make_running_agent()
            registry._agents[agent_id] = agent
            return agent

        request_event = _request_event(StartRequest(agent_id="a-001", reason="boot"))

        with patch.object(registry, "_start_agent", side_effect=fake_start) as mock_start:
            await registry._on_start_request(request_event)
            await asyncio.sleep(0.05)

        mock_start.assert_awaited_once_with("a-001")
        assert "a-001" in registry._agents
        published_types = [call.args[0].type for call in bus.publish.await_args_list]
        assert EventType.AGENT_WAKING not in published_types
        assert EventType.AGENT_WOKEN not in published_types
        assert EventType.AGENT_WAKE_FAILED not in published_types

        await registry.shutdown()

    @pytest.mark.asyncio
    async def test_concurrent_wake_requests_start_once(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="lazy")}
        registry, _bus = _make_registry(configs, temp_paths)
        registry.register("a-001")
        await registry.initialize()

        async def fake_start(agent_id: str):
            agent = _make_running_agent()
            registry._agents[agent_id] = agent
            return agent

        # Two wake requests for the same persona dispatched back-to-back
        trigger1 = _routed_event(["a-001"], ["a-001"], content="first")
        trigger2 = _routed_event(["a-001"], ["a-001"], content="second")

        with patch.object(registry, "_start_agent", side_effect=fake_start) as mock_start:
            # No yield between the two handler calls: the second must see
            # the agent already waking and skip
            await registry._on_start_request(
                _request_event(StartRequest("a-001", "wake", trigger1))
            )
            await registry._on_start_request(
                _request_event(StartRequest("a-001", "wake", trigger2))
            )
            await asyncio.sleep(0.05)

        mock_start.assert_awaited_once()
        assert len(registry._agents) == 1

        await registry.shutdown()

    @pytest.mark.asyncio
    async def test_wake_lock_serializes_and_redelivers_both(self, temp_paths):
        """Two _fulfill_start_request calls race on the lock: one assembly,
        both trigger events redelivered (the second event arrived while the
        agent was down and would otherwise be lost)."""
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="lazy")}
        registry, bus = _make_registry(configs, temp_paths)
        registry.register("a-001")
        await registry.initialize()

        started = asyncio.Event()

        async def fake_start(agent_id: str):
            await started.wait()  # Hold the first wake until both racers queued
            agent = _make_running_agent()
            registry._agents[agent_id] = agent
            return agent

        trigger1 = _routed_event(["a-001"], ["a-001"], content="first")
        trigger2 = _routed_event(["a-001"], ["a-001"], content="second")

        with patch.object(registry, "_start_agent", side_effect=fake_start):
            task1 = asyncio.create_task(
                registry._fulfill_start_request(StartRequest("a-001", "wake", trigger1))
            )
            task2 = asyncio.create_task(
                registry._fulfill_start_request(StartRequest("a-001", "wake", trigger2))
            )
            await asyncio.sleep(0.05)
            started.set()
            await asyncio.gather(task1, task2)

        assert len(registry._agents) == 1
        published = [call.args[0] for call in bus.publish.await_args_list]
        assert trigger1 in published
        assert trigger2 in published

        await registry.shutdown()

    @pytest.mark.asyncio
    async def test_running_agent_is_not_started(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="lazy")}
        registry, _bus = _make_registry(configs, temp_paths)
        registry.register("a-001")
        registry._agents["a-001"] = _make_running_agent()

        trigger = _routed_event(["a-001"], ["a-001"])
        with patch.object(registry, "_start_agent", new=AsyncMock()) as mock_start:
            await registry._on_start_request(
                _request_event(StartRequest("a-001", "wake", trigger))
            )
            await asyncio.sleep(0.05)

        mock_start.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_agent_request_is_ignored(self, temp_paths):
        registry, _bus = _make_registry({}, temp_paths)
        trigger = _routed_event(["ghost"], ["ghost"])
        with patch.object(registry, "_start_agent", new=AsyncMock()) as mock_start:
            await registry._on_start_request(
                _request_event(StartRequest("ghost", "wake", trigger))
            )
            await asyncio.sleep(0.05)
        mock_start.assert_not_called()

    @pytest.mark.asyncio
    async def test_wake_failure_publishes_event(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="lazy")}
        registry, bus = _make_registry(configs, temp_paths)
        registry.register("a-001")
        await registry.initialize()

        trigger = _routed_event(["a-001"], ["a-001"])

        with patch.object(registry, "_start_agent", new=AsyncMock(return_value=None)):
            await registry._fulfill_start_request(StartRequest("a-001", "wake", trigger))

        published_types = [call.args[0].type for call in bus.publish.await_args_list]
        assert EventType.AGENT_WAKE_FAILED in published_types
        assert EventType.AGENT_WOKEN not in published_types
        # Failed wake must not redeliver (the agent cannot receive it)
        assert trigger not in [call.args[0] for call in bus.publish.await_args_list]
        assert "a-001" not in registry._waking

        await registry.shutdown()


class TestIdleFactAndDrain:
    """Idle lazy agents are *reported* (AGENT_IDLE fact) by the monitor;
    stopping them happens only via AGENT_STOP_REQUEST (drain execution).
    Live sessions veto both."""

    def _registry_with_lazy_agent(
        self, temp_paths: TlonPaths, **agent_kwargs
    ) -> tuple[AgentRegistry, MagicMock, MagicMock]:
        configs = {
            "a-001": AgentConfig(
                id="a-001", name="甲", activation="lazy", idle_timeout_seconds=600
            )
        }
        registry, bus = _make_registry(configs, temp_paths)
        registry.register("a-001")
        agent = _make_running_agent(**agent_kwargs)
        registry._agents["a-001"] = agent
        return registry, bus, agent

    # ---------- Fact detection (mechanism) ----------

    @pytest.mark.asyncio
    async def test_idle_lazy_agent_is_reported_not_stopped(self, temp_paths):
        registry, bus, agent = self._registry_with_lazy_agent(
            temp_paths, active_sessions=0, idle_seconds=9999.0
        )

        await registry._report_if_idle("a-001")

        # Fact only: the agent keeps running
        assert "a-001" in registry._agents
        agent.shutdown.assert_not_called()
        published = [call.args[0] for call in bus.publish.await_args_list]
        idle_events = [e for e in published if e.type == EventType.AGENT_IDLE]
        assert len(idle_events) == 1
        assert idle_events[0].payload["agent_id"] == "a-001"
        assert idle_events[0].payload["idle_seconds"] == 9999

    @pytest.mark.asyncio
    async def test_live_session_suppresses_idle_fact(self, temp_paths):
        registry, bus, _agent = self._registry_with_lazy_agent(
            temp_paths, active_sessions=1, idle_seconds=9999.0
        )

        await registry._report_if_idle("a-001")

        bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_recently_active_agent_is_not_reported(self, temp_paths):
        registry, bus, _agent = self._registry_with_lazy_agent(
            temp_paths, active_sessions=0, idle_seconds=10.0
        )

        await registry._report_if_idle("a-001")

        bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_always_agent_is_never_reported(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="always")}
        registry, bus = _make_registry(configs, temp_paths)
        registry.register("a-001")
        agent = _make_running_agent(active_sessions=0, idle_seconds=99999.0)
        registry._agents["a-001"] = agent

        await registry._report_if_idle("a-001")

        bus.publish.assert_not_called()

    # ---------- Drain execution (stop request) ----------

    @pytest.mark.asyncio
    async def test_stop_request_drains_idle_agent(self, temp_paths):
        registry, bus, agent = self._registry_with_lazy_agent(
            temp_paths, active_sessions=0, idle_seconds=9999.0
        )

        await registry._drain_agent("a-001", "drain")

        assert "a-001" not in registry._agents
        agent.shutdown.assert_awaited_once()
        published_types = [call.args[0].type for call in bus.publish.await_args_list]
        assert EventType.AGENT_SLEEPING in published_types

    @pytest.mark.asyncio
    async def test_live_session_vetoes_drain(self, temp_paths):
        registry, bus, agent = self._registry_with_lazy_agent(
            temp_paths, active_sessions=1, idle_seconds=9999.0
        )

        await registry._drain_agent("a-001", "drain")

        assert "a-001" in registry._agents
        agent.shutdown.assert_not_called()
        bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_recently_active_agent_survives_drain(self, temp_paths):
        """The fact motivating the request may be stale: activity since the
        AGENT_IDLE report vetoes the drain."""
        registry, bus, agent = self._registry_with_lazy_agent(
            temp_paths, active_sessions=0, idle_seconds=10.0
        )

        await registry._drain_agent("a-001", "drain")

        assert "a-001" in registry._agents
        agent.shutdown.assert_not_called()
        bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_drain_rechecks_sessions_under_lock(self, temp_paths):
        """A session starting in the race window (while the drain waits on
        the per-agent lock) keeps the agent alive."""
        registry, _bus, agent = self._registry_with_lazy_agent(
            temp_paths, active_sessions=0, idle_seconds=9999.0
        )

        lock = registry._wake_locks.setdefault("a-001", asyncio.Lock())
        await lock.acquire()
        drain_task = asyncio.create_task(registry._drain_agent("a-001", "drain"))
        await asyncio.sleep(0.05)  # drain passed first check, waits on lock

        agent.active_session_count = 1  # session starts in the race window
        lock.release()
        await drain_task

        assert "a-001" in registry._agents
        agent.shutdown.assert_not_called()

    @pytest.mark.asyncio
    async def test_monitor_loop_reports_idle_agent(self, temp_paths):
        """Mechanism test: the monitor reports idle agents via AGENT_IDLE;
        only an explicit stop request drains the agent."""
        configs = {
            "a-001": AgentConfig(
                id="a-001", name="甲", activation="lazy", idle_timeout_seconds=0
            )
        }
        registry, bus = _make_registry(configs, temp_paths)
        registry.register("a-001")
        registry._IDLE_CHECK_INTERVAL_SECONDS = 0.05
        await registry.initialize()

        agent = _make_running_agent(active_sessions=0, idle_seconds=9999.0)
        registry._agents["a-001"] = agent

        await asyncio.sleep(0.3)

        # Reported, not drained
        assert "a-001" in registry._agents
        agent.shutdown.assert_not_called()
        published_types = [call.args[0].type for call in bus.publish.await_args_list]
        assert EventType.AGENT_IDLE in published_types

        # The stop request executes the drain
        await registry._on_stop_request(
            _request_event(StopRequest(agent_id="a-001", reason="drain"))
        )
        await asyncio.sleep(0.05)

        assert "a-001" not in registry._agents
        agent.shutdown.assert_awaited_once()

        await registry.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_monitor_and_unsubscribes(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="lazy")}
        registry, bus = _make_registry(configs, temp_paths)
        registry.register("a-001")
        await registry.initialize()

        monitor = registry._idle_monitor_task
        assert monitor is not None

        await registry.shutdown()

        assert monitor.cancelled()
        assert registry._idle_monitor_task is None
        assert bus.unsubscribe.call_count == 2
        bus.unsubscribe.assert_any_call("sub-start")
        bus.unsubscribe.assert_any_call("sub-stop")
        assert registry._request_subscriptions == []


class TestDockerLazyStart:
    """Container starts on first tool use, not at agent assembly."""

    @pytest.mark.asyncio
    async def test_start_agent_does_not_ensure_running(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        registry, _bus = _make_registry(configs, temp_paths)
        registry.register("a-001")

        with (
            patch("tlon.agents.agent_registry.DockerRuntime") as mock_docker_cls,
            patch("tlon.agents.agent_registry.MemoryManager") as mock_memory_cls,
            patch("tlon.agents.agent_registry.TaskManager") as mock_tm_cls,
        ):
            mock_memory = MagicMock()
            mock_memory.load = AsyncMock()
            mock_memory_cls.return_value = mock_memory
            mock_tm = MagicMock()
            mock_tm_cls.return_value = mock_tm

            docker_runtime = MagicMock()
            mock_docker_cls.return_value = docker_runtime

            agent = await registry._start_agent("a-001")

        assert agent is not None
        # Container is constructed but NOT started during assembly
        docker_runtime.ensure_running.assert_not_called()

        await agent.shutdown()

    @pytest.mark.asyncio
    async def test_execute_ensures_running_on_first_use(self):
        """DockerRuntime.execute lazily starts the container on first use."""
        runtime = DockerRuntime(DockerRuntimeConfig(), agent_id="a-001")
        runtime.client = MagicMock()
        runtime.container = None

        container = MagicMock()
        container.exec_run.return_value = MagicMock(exit_code=0, output=(b"ok", b""))

        async def fake_ensure_running():
            runtime.container = container

        runtime.ensure_running = AsyncMock(side_effect=fake_ensure_running)

        result = await runtime.execute("echo ok")

        runtime.ensure_running.assert_awaited_once()
        assert result.exit_code == 0
        assert result.stdout == "ok"
