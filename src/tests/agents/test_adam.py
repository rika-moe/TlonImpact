"""Tests for Adam — the built-in lifecycle policy member (阶段 D).

Covers the three policy lines:
- boot: initialize() issues AGENT_START_REQUEST{reason="boot"} only for
  non-lazy agents that are not already running
- wake: MESSAGE_ROUTED mention of a sleeping agent issues
  AGENT_START_REQUEST{reason="wake", trigger_event} with the trigger's
  trace carrier; running agents, unknown ids and human ids never do
- drain: AGENT_IDLE facts become AGENT_STOP_REQUEST{reason="drain"}

Plus an end-to-end test over a real EventBus wiring Adam + registry:
mention → request → fulfillment → trigger redelivery, and the redelivered
event does not trigger a second request (loop guard).
"""

import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.agents.adam import Adam
from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.lifecycle import StartRequest, StopRequest
from tlon.base.config import AgentConfig
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.messaging.message import Message
from tlon.organization.messaging.router import RoutedMessage

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
    bus.subscribe = MagicMock(side_effect=lambda *_args, **_kwargs: "sub-x")
    bus.unsubscribe = MagicMock()
    return bus


def _make_running_agent(active_sessions: int = 0, idle_seconds: float = 0.0) -> MagicMock:
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


@pytest.fixture
def temp_paths():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield TlonPaths(home=tmpdir)


def _make_registry(
    configs: dict[str, AgentConfig], temp_paths: TlonPaths, bus: MagicMock | None = None
) -> tuple[AgentRegistry, MagicMock]:
    bus = bus or _make_bus()
    registry = AgentRegistry(
        event_bus=bus,
        agent_info_provider=MockAgentInfoProvider(configs),
        message_store=MagicMock(),
        paths=temp_paths,
    )
    return registry, bus


def _start_requests(bus: MagicMock) -> list[Event]:
    return [
        call.args[0]
        for call in bus.publish.await_args_list
        if call.args[0].type == EventType.AGENT_START_REQUEST
    ]


class TestAdamIdentity:
    def test_member_identity(self, temp_paths):
        registry, bus = _make_registry({}, temp_paths)
        adam = Adam(event_bus=bus, agent_registry=registry)

        assert adam.member_id == "adam"
        assert adam.member_type == "script"
        assert adam.triggers_proactive is False
        assert adam.get_state() == "offline"


class TestBootPolicy:
    @pytest.mark.asyncio
    async def test_boot_requests_only_always_not_running(self, temp_paths):
        configs = {
            "a-001": AgentConfig(id="a-001", name="甲", activation="always"),
            "a-002": AgentConfig(id="a-002", name="乙", activation="lazy"),
            "a-003": AgentConfig(id="a-003", name="丙", activation="always"),
        }
        registry, bus = _make_registry(configs, temp_paths)
        registry.register_all(["a-001", "a-002", "a-003"])
        registry._agents["a-003"] = _make_running_agent()  # already running

        adam = Adam(event_bus=bus, agent_registry=registry)
        await adam.initialize()

        requests = _start_requests(bus)
        assert len(requests) == 1
        request = requests[0].payload
        assert isinstance(request, StartRequest)
        assert request.agent_id == "a-001"
        assert request.reason == "boot"
        assert request.trigger_event is None
        assert adam.get_state() == "active"

    @pytest.mark.asyncio
    async def test_boot_all_lazy_issues_no_requests(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="lazy")}
        registry, bus = _make_registry(configs, temp_paths)
        registry.register("a-001")

        adam = Adam(event_bus=bus, agent_registry=registry)
        await adam.initialize()

        assert _start_requests(bus) == []


class TestWakePolicy:
    @pytest.mark.asyncio
    async def test_mention_of_sleeping_agent_requests_wake(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="lazy")}
        registry, bus = _make_registry(configs, temp_paths)
        registry.register("a-001")
        adam = Adam(event_bus=bus, agent_registry=registry)
        await adam.initialize()

        trigger = _routed_event(["a-001"], ["a-001"])
        await adam._on_message_routed(trigger)

        requests = _start_requests(bus)
        assert len(requests) == 1
        request_event = requests[0]
        request = request_event.payload
        assert isinstance(request, StartRequest)
        assert request.agent_id == "a-001"
        assert request.reason == "wake"
        # The trigger is carried for redelivery after the start
        assert request.trigger_event is trigger
        # The request joins the trigger's trace tree
        assert request_event.trace_id == TRACE_ID
        assert request_event.span_id == SPAN_ID

    @pytest.mark.asyncio
    async def test_mention_of_running_agent_issues_no_request(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="lazy")}
        registry, bus = _make_registry(configs, temp_paths)
        registry.register("a-001")
        registry._agents["a-001"] = _make_running_agent()
        adam = Adam(event_bus=bus, agent_registry=registry)
        await adam.initialize()

        await adam._on_message_routed(_routed_event(["a-001"], ["a-001"]))

        assert _start_requests(bus) == []

    @pytest.mark.asyncio
    async def test_mention_of_unknown_id_issues_no_request(self, temp_paths):
        registry, bus = _make_registry({}, temp_paths)
        adam = Adam(event_bus=bus, agent_registry=registry)
        await adam.initialize()

        await adam._on_message_routed(_routed_event(["ghost"], ["ghost"]))

        assert _start_requests(bus) == []

    @pytest.mark.asyncio
    async def test_mention_of_human_id_issues_no_request(self, temp_paths):
        """Human members (human:<name>) have no agent config — no wake."""
        registry, bus = _make_registry({}, temp_paths)
        adam = Adam(event_bus=bus, agent_registry=registry)
        await adam.initialize()

        await adam._on_message_routed(_routed_event(["human:alice"], ["human:alice"]))

        assert _start_requests(bus) == []

    @pytest.mark.asyncio
    async def test_proactive_broadcast_issues_no_request(self, temp_paths):
        configs = {
            "a-001": AgentConfig(id="a-001", name="甲", activation="lazy", proactive=True),
        }
        registry, bus = _make_registry(configs, temp_paths)
        registry.register("a-001")
        adam = Adam(event_bus=bus, agent_registry=registry)
        await adam.initialize()

        # Proactive broadcast: agent is a target but not a mention target
        await adam._on_message_routed(_routed_event(["a-001"], []))

        assert _start_requests(bus) == []

    @pytest.mark.asyncio
    async def test_non_routed_payload_is_ignored(self, temp_paths):
        registry, bus = _make_registry({}, temp_paths)
        adam = Adam(event_bus=bus, agent_registry=registry)
        await adam.initialize()

        await adam._on_message_routed(
            Event(type=EventType.MESSAGE_ROUTED, payload={"not": "routed"}, source="x")
        )

        assert _start_requests(bus) == []


class TestDrainPolicy:
    @pytest.mark.asyncio
    async def test_agent_idle_fact_becomes_stop_request(self, temp_paths):
        registry, bus = _make_registry({}, temp_paths)
        adam = Adam(event_bus=bus, agent_registry=registry)
        await adam.initialize()

        await adam._on_agent_idle(
            Event(
                type=EventType.AGENT_IDLE,
                payload={"agent_id": "a-001", "idle_seconds": 9999},
                source="agent_registry",
            )
        )

        stop_events = [
            call.args[0]
            for call in bus.publish.await_args_list
            if call.args[0].type == EventType.AGENT_STOP_REQUEST
        ]
        assert len(stop_events) == 1
        request = stop_events[0].payload
        assert isinstance(request, StopRequest)
        assert request.agent_id == "a-001"
        assert request.reason == "drain"

    @pytest.mark.asyncio
    async def test_malformed_idle_event_is_ignored(self, temp_paths):
        registry, bus = _make_registry({}, temp_paths)
        adam = Adam(event_bus=bus, agent_registry=registry)
        await adam.initialize()

        await adam._on_agent_idle(
            Event(type=EventType.AGENT_IDLE, payload="garbage", source="agent_registry")
        )
        await adam._on_agent_idle(
            Event(type=EventType.AGENT_IDLE, payload={}, source="agent_registry")
        )

        bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_shutdown_unsubscribes(self, temp_paths):
        registry, bus = _make_registry({}, temp_paths)
        adam = Adam(event_bus=bus, agent_registry=registry)
        await adam.initialize()
        assert adam.get_state() == "active"

        await adam.shutdown()

        assert bus.unsubscribe.call_count == 2  # MESSAGE_ROUTED + AGENT_IDLE
        assert adam.get_state() == "offline"


class TestEndToEnd:
    """Adam + registry over a real EventBus: policy and mechanism connect
    only through request/fact events."""

    @pytest.mark.asyncio
    async def test_mention_wakes_and_redelivers_once(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="lazy")}
        bus = EventBus()
        await bus.initialize()
        registry, _ = _make_registry(configs, temp_paths, bus=bus)
        registry.register("a-001")
        await registry.initialize()
        adam = Adam(event_bus=bus, agent_registry=registry)
        await adam.initialize()

        routed_seen: list[Event] = []
        bus.subscribe(EventType.MESSAGE_ROUTED, lambda e: routed_seen.append(e))
        requests_seen: list[Event] = []
        bus.subscribe(EventType.AGENT_START_REQUEST, lambda e: requests_seen.append(e))

        async def fake_start(agent_id: str):
            agent = _make_running_agent()
            registry._agents[agent_id] = agent
            return agent

        trigger = _routed_event(["a-001"], ["a-001"])
        with patch.object(registry, "_start_agent", side_effect=fake_start):
            await bus.publish(trigger)
            await asyncio.sleep(0.3)

        assert "a-001" in registry._agents
        # Original delivery + registry redelivery, same event object
        assert len(routed_seen) == 2
        assert routed_seen[0] is trigger
        assert routed_seen[1] is trigger
        # Loop guard: the redelivered mention saw the agent running and
        # did not trigger a second request
        assert len(requests_seen) == 1

        await adam.shutdown()
        await registry.shutdown()
        await bus.shutdown()

    @pytest.mark.asyncio
    async def test_idle_fact_drains_agent(self, temp_paths):
        configs = {
            "a-001": AgentConfig(
                id="a-001", name="甲", activation="lazy", idle_timeout_seconds=600
            )
        }
        bus = EventBus()
        await bus.initialize()
        registry, _ = _make_registry(configs, temp_paths, bus=bus)
        registry.register("a-001")
        await registry.initialize()
        adam = Adam(event_bus=bus, agent_registry=registry)
        await adam.initialize()

        agent = _make_running_agent(active_sessions=0, idle_seconds=9999.0)
        registry._agents["a-001"] = agent

        sleeping_seen: list[Event] = []
        bus.subscribe(EventType.AGENT_SLEEPING, lambda e: sleeping_seen.append(e))

        # The registry's idle monitor is the only AGENT_IDLE source in
        # production; here we publish the fact directly to keep the test fast
        await bus.publish(
            Event(
                type=EventType.AGENT_IDLE,
                payload={"agent_id": "a-001", "idle_seconds": 9999},
                source="agent_registry",
            )
        )
        await asyncio.sleep(0.3)

        assert "a-001" not in registry._agents
        agent.shutdown.assert_awaited_once()
        assert len(sleeping_seen) == 1

        await adam.shutdown()
        await registry.shutdown()
        await bus.shutdown()
