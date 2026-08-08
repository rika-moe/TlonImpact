"""Tests for the registry's runtime split (P5b: react/script).

Covers script-member assembly through ``_start_agent`` (spec total
semantics: missing/invalid script.tlon refuses the start), hot adoption,
status reporting, the wake path, contractor stop, idle drain, and
end-to-end spawn of the built-in relay prototype.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.lifecycle import StartRequest
from tlon.agents.prototypes import instantiate, seed_prototypes
from tlon.agents.script_member import ScriptMember
from tlon.base.config import AgentConfig, ConfigService
from tlon.base.events.event import Event, EventType
from tlon.base.paths import TlonPaths

SCRIPT_EDN = """\
{:version 1
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["gu-yanxi@tlon.internal"]})]}]}
"""


def _make_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock()
    bus.subscribe = MagicMock(return_value="sub-1")
    bus.unsubscribe = MagicMock()
    return bus


@pytest.fixture
def temp_paths(tmp_path, monkeypatch) -> TlonPaths:
    """Isolated tlon home; ScriptSpec.load resolves through the patched paths."""
    tlon_paths = TlonPaths(home=tmp_path)
    seed_prototypes(tlon_paths)
    monkeypatch.setattr("tlon.agents.script_spec.paths", tlon_paths)
    return tlon_paths


async def _make_registry(
    temp_paths: TlonPaths,
    configs: dict[str, AgentConfig] | None = None,
    **budget_kwargs,
) -> tuple[AgentRegistry, MagicMock, ConfigService]:
    """Registry over a real ConfigService backed by an isolated tlon home."""
    bus = _make_bus()
    provider = ConfigService(event_bus=bus, tlon_home=temp_paths.tlon_home())
    await provider.initialize()
    for config in (configs or {}).values():
        provider.save_agent(config)
    registry = AgentRegistry(
        event_bus=bus,
        agent_info_provider=provider,
        paths=temp_paths,
        **budget_kwargs,
    )
    for agent_id in (configs or {}):
        registry.register(agent_id)
    return registry, bus, provider


def _write_script(temp_paths: TlonPaths, agent_id: str, content: str = SCRIPT_EDN) -> None:
    script_file = temp_paths.agent_config_dir(agent_id) / "script.tlon"
    script_file.write_text(content, encoding="utf-8")


def _published(bus: MagicMock, event_type: EventType) -> list:
    return [c.args[0] for c in bus.publish.await_args_list if c.args[0].type == event_type]


class TestScriptAssembly:
    async def test_script_config_starts_script_member(self, temp_paths):
        config = AgentConfig(id="rl-001", name="转发", runtime="script")
        registry, bus, _ = await _make_registry(temp_paths, {"rl-001": config})
        _write_script(temp_paths, "rl-001")

        member = await registry._start_agent("rl-001")

        assert isinstance(member, ScriptMember)
        assert registry.get_agent("rl-001") is member
        started = _published(bus, EventType.AGENT_STARTED)
        assert len(started) == 1
        assert started[0].payload["runtime"] == "script"

    async def test_missing_script_file_refuses_start(self, temp_paths):
        config = AgentConfig(id="rl-002", name="转发", runtime="script")
        registry, bus, _ = await _make_registry(temp_paths, {"rl-002": config})

        member = await registry._start_agent("rl-002")

        assert member is None
        assert registry.get_agent("rl-002") is None
        assert _published(bus, EventType.AGENT_STARTED) == []

    async def test_invalid_script_file_refuses_start(self, temp_paths):
        config = AgentConfig(id="rl-003", name="转发", runtime="script")
        registry, _, _ = await _make_registry(temp_paths, {"rl-003": config})
        _write_script(temp_paths, "rl-003", content="{:version 1}")  # missing :rules

        member = await registry._start_agent("rl-003")

        assert member is None

    async def test_react_config_does_not_take_script_path(self, temp_paths):
        """Default runtime stays react: assembly fails on the missing
        behavior file, not on the missing script file."""
        config = AgentConfig(id="rx-001", name="反应")
        registry, _, _ = await _make_registry(temp_paths, {"rx-001": config})
        _write_script(temp_paths, "rx-001")  # script.tlon exists but is ignored

        agent = await registry._start_agent("rx-001")

        assert agent is None  # react path requires behavior.tlon


class TestScriptLifecycle:
    async def test_adopt_script_agent(self, temp_paths):
        registry, _, _ = await _make_registry(temp_paths)
        instantiate(
            "relay", "rl-010", "转发",
            tlon_paths=temp_paths,
            params={"to": ["a@tlon.internal"]},
        )

        result = await registry.adopt_agent("rl-010")

        assert result == "started"
        assert isinstance(registry.get_agent("rl-010"), ScriptMember)

    async def test_get_agent_status_for_script_member(self, temp_paths):
        config = AgentConfig(
            id="rl-011", name="转发", runtime="script", activation="lazy"
        )
        registry, _, _ = await _make_registry(temp_paths, {"rl-011": config})
        assert registry.get_agent_status("rl-011")["state"] == "sleeping"
        _write_script(temp_paths, "rl-011")
        await registry._start_agent("rl-011")

        status = registry.get_agent_status("rl-011")

        assert status["state"] == "active"
        assert status["role"] == "resident"

    async def test_wake_path_starts_script_member(self, temp_paths):
        config = AgentConfig(
            id="rl-012", name="转发", runtime="script", activation="lazy"
        )
        registry, bus, _ = await _make_registry(temp_paths, {"rl-012": config})
        _write_script(temp_paths, "rl-012")
        request_event = Event(
            type=EventType.AGENT_START_REQUEST,
            payload=StartRequest(agent_id="rl-012", reason="wake"),
            source="adam",
        )

        await registry._fulfill_start_request(request_event.payload, request_event)

        assert isinstance(registry.get_agent("rl-012"), ScriptMember)
        assert len(_published(bus, EventType.AGENT_WOKEN)) == 1

    async def test_stop_script_contractor_discards_home(self, temp_paths):
        registry, _, provider = await _make_registry(
            temp_paths, contractor_idle_timeout_seconds=300
        )
        config = await registry.spawn_contractor(
            "relay", "转发", spawner_id="root", session_id=None,
            params={"to": ["a@tlon.internal"]},
        )
        agent_id = config.id
        assert isinstance(registry.get_agent(agent_id), ScriptMember)

        stopped = await registry.stop_contractor(agent_id, stopped_by="root")

        assert stopped is True
        assert registry.get_agent(agent_id) is None
        assert provider.get_config(agent_id) is None
        assert not (temp_paths.tlon_home() / agent_id).exists()

    async def test_idle_drain_discards_script_contractor(self, temp_paths):
        registry, bus, provider = await _make_registry(
            temp_paths, contractor_idle_timeout_seconds=0
        )
        config = await registry.spawn_contractor(
            "relay", "转发", spawner_id="root", session_id=None,
            params={"to": ["a@tlon.internal"]},
        )
        agent_id = config.id
        assert registry.get_agent(agent_id) is not None

        await registry._drain_agent(agent_id, reason="drain")

        assert registry.get_agent(agent_id) is None
        assert provider.get_config(agent_id) is None
        assert not (temp_paths.tlon_home() / agent_id).exists()
        stopped = _published(bus, EventType.AGENT_STOPPED)
        assert any(event.payload["agent_id"] == agent_id for event in stopped)


class TestRelaySpawnEndToEnd:
    async def test_spawn_relay_forwards_incoming_mail(self, temp_paths):
        """The full path: spawn from the built-in relay prototype, then a
        delivered mail comes out as a forwarded MAIL_NEW."""
        from tlon.organization.mail.mail import Mail

        registry, bus, _ = await _make_registry(temp_paths)
        config = await registry.spawn_contractor(
            "relay", "转发", spawner_id="root", session_id=None,
            params={"to": ["gu-yanxi@tlon.internal"]},
        )

        assert config.runtime == "script"
        assert config.role == "contractor"
        member = registry.get_agent(config.id)
        assert isinstance(member, ScriptMember)
        spawned = _published(bus, EventType.AGENT_SPAWNED)
        assert spawned[0].payload["started"] is True

        original = Mail.create(
            from_address="xie-yunshu@tlon.internal",
            to=[member.address],
            content="请查收",
        )
        await member._on_event(
            Event(
                type=EventType.MAIL_DELIVERED,
                payload={"mail": original, "recipient": config.id},
                source="mail_delivery",
            )
        )

        forwarded = [
            event.payload
            for event in _published(bus, EventType.MAIL_NEW)
            if getattr(event.payload, "forward_of", None) == original.id
        ]
        assert len(forwarded) == 1
        assert forwarded[0].to == ["gu-yanxi@tlon.internal"]
        assert forwarded[0].content == "请查收"
