"""Tests for spawn/stop of contractors (阶段 6b: spawn/stop 工具化 + spawn 预算).

Covers:
- spawn success path: config visible, started immediately, identity fields
  (role/depth/spawned_by), AGENT_SPAWNED event, behavior overrides merged
- budget rejections: max_activations / max_spawn_depth / session quota,
  with quota isolated per session
- stop: contractor stopped and home dir deleted; live sessions veto;
  residents refused; unknown agents refused
- orphan policy: a contractor survives its spawner going away
- idle drain: contractor reaped -> home dir deleted; resident sleeps -> kept
- tool layer: schemas, error shapes, contextvars session attribution
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.contextvars import bound_contextvars

from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.edn import Keyword, loads
from tlon.agents.prototypes import PrototypeError, seed_prototypes
from tlon.agents.spawn_errors import SpawnRejectedError, StopRejectedError
from tlon.base.config import AgentConfig, ConfigService
from tlon.base.events.event import EventType
from tlon.base.paths import TlonPaths
from tlon.tools.agents.list_agents import ListAgentsTool
from tlon.tools.agents.spawn_agent import SpawnAgentTool, _ednify_keys
from tlon.tools.agents.stop_agent import StopAgentTool

RESIDENT = "resident-1"
PROTOTYPE = "deep-thinker"


def _make_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock()
    bus.subscribe = MagicMock(return_value="sub-1")
    bus.unsubscribe = MagicMock()
    return bus


def _make_running_agent(active_sessions: int = 0, idle_seconds: float = 0.0) -> MagicMock:
    agent = MagicMock()
    agent.shutdown = AsyncMock()
    agent.get_state = MagicMock(return_value="active")
    agent.active_session_count = active_sessions
    agent.idle_seconds = idle_seconds
    return agent


@pytest.fixture
def temp_paths():
    with tempfile.TemporaryDirectory() as tmpdir:
        tlon_paths = TlonPaths(home=Path(tmpdir))
        seed_prototypes(tlon_paths)
        yield tlon_paths


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
        message_store=MagicMock(),
        paths=temp_paths,
        **budget_kwargs,
    )
    for agent_id in (configs or {}):
        registry.register(agent_id)
    return registry, bus, provider


def _patch_start(registry: AgentRegistry, **agent_kwargs) -> AsyncMock:
    """Fake _start_agent: inserts a mock agent into the run table."""

    async def fake_start(agent_id: str):
        agent = _make_running_agent(**agent_kwargs)
        registry._agents[agent_id] = agent
        return agent

    return AsyncMock(side_effect=fake_start)


def _published(bus: MagicMock, event_type: EventType) -> list:
    return [c.args[0] for c in bus.publish.await_args_list if c.args[0].type == event_type]


class TestSpawnSuccess:
    async def test_spawn_starts_immediately_with_contractor_identity(self, temp_paths):
        configs = {RESIDENT: AgentConfig(id=RESIDENT, name="居民甲")}
        registry, bus, provider = await _make_registry(temp_paths, configs)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(registry, "_start_agent", _patch_start(registry))
            config = await registry.spawn_contractor(
                prototype=PROTOTYPE, name="临时工甲", spawner_id=RESIDENT, session_id="s-1"
            )

        # Identity fields
        assert config.id.startswith(f"c-{PROTOTYPE}-")
        assert config.role == "contractor"
        assert config.spawned_by == RESIDENT
        assert config.spawn_depth == 1
        assert config.activation == "lazy"
        assert config.idle_timeout_seconds == 300

        # Visible to the provider (router/status), registered, and running
        assert provider.get_config(config.id) is not None
        assert config.id in registry._registered_ids
        assert config.id in registry._agents

        # On-disk YAML carries the final contractor config
        reloaded = ConfigService(event_bus=_make_bus(), tlon_home=temp_paths.tlon_home())
        await reloaded.initialize()
        assert reloaded.get_config(config.id).role == "contractor"

        # AGENT_SPAWNED event with spawner/prototype/depth
        spawned = _published(bus, EventType.AGENT_SPAWNED)
        assert len(spawned) == 1
        payload = spawned[0].payload
        assert payload["agent_id"] == config.id
        assert payload["spawner_id"] == RESIDENT
        assert payload["prototype"] == PROTOTYPE
        assert payload["spawn_depth"] == 1
        assert payload["started"] is True

        # Quota ledger recorded the spawn
        assert registry._spawn_quota[(RESIDENT, "s-1")] == 1

    async def test_spawn_depth_accumulates_down_the_chain(self, temp_paths):
        registry, _bus, _provider = await _make_registry(temp_paths)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(registry, "_start_agent", _patch_start(registry))
            first = await registry.spawn_contractor(
                prototype=PROTOTYPE, name="一", spawner_id=RESIDENT, session_id=None
            )
            second = await registry.spawn_contractor(
                prototype=PROTOTYPE, name="二", spawner_id=first.id, session_id=None
            )

        assert first.spawn_depth == 1
        assert second.spawn_depth == 2
        assert second.spawned_by == first.id

    async def test_spawn_merges_behavior_overrides(self, temp_paths):
        registry, _bus, _provider = await _make_registry(temp_paths)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(registry, "_start_agent", _patch_start(registry))
            config = await registry.spawn_contractor(
                prototype=PROTOTYPE,
                name="临时工甲",
                spawner_id=RESIDENT,
                session_id=None,
                overrides={Keyword("loop"): {Keyword("max-iterations"): 3}},
            )

        behavior = loads(
            (temp_paths.agent_config_dir(config.id) / "behavior.tlon").read_text(
                encoding="utf-8"
            )
        )
        assert behavior[Keyword("loop")][Keyword("max-iterations")] == 3

    async def test_spawn_unknown_prototype_raises(self, temp_paths):
        registry, _bus, _provider = await _make_registry(temp_paths)
        with pytest.raises(PrototypeError):
            await registry.spawn_contractor(
                prototype="no-such-proto",
                name="临时工甲",
                spawner_id=RESIDENT,
                session_id=None,
            )


class TestSpawnBudget:
    async def test_max_activations_rejects_spawn(self, temp_paths):
        registry, _bus, _provider = await _make_registry(temp_paths, max_activations=2)
        registry._agents["a"] = _make_running_agent()
        registry._agents["b"] = _make_running_agent()

        with pytest.raises(SpawnRejectedError) as exc_info:
            await registry.spawn_contractor(
                prototype=PROTOTYPE, name="甲", spawner_id=RESIDENT, session_id=None
            )
        assert exc_info.value.reason == "max_activations"

    async def test_max_spawn_depth_rejects_spawn(self, temp_paths):
        spawner = AgentConfig(
            id="c-x-00000000", name="深层临时工", role="contractor", spawn_depth=3
        )
        registry, _bus, _provider = await _make_registry(
            temp_paths, {"c-x-00000000": spawner}, max_spawn_depth=3
        )

        with pytest.raises(SpawnRejectedError) as exc_info:
            await registry.spawn_contractor(
                prototype=PROTOTYPE, name="甲", spawner_id=spawner.id, session_id=None
            )
        assert exc_info.value.reason == "max_spawn_depth"

    async def test_session_quota_rejects_third_spawn(self, temp_paths):
        registry, _bus, _provider = await _make_registry(temp_paths, session_spawn_quota=2)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(registry, "_start_agent", _patch_start(registry))
            await registry.spawn_contractor(
                prototype=PROTOTYPE, name="一", spawner_id=RESIDENT, session_id="s-1"
            )
            await registry.spawn_contractor(
                prototype=PROTOTYPE, name="二", spawner_id=RESIDENT, session_id="s-1"
            )
            with pytest.raises(SpawnRejectedError) as exc_info:
                await registry.spawn_contractor(
                    prototype=PROTOTYPE, name="三", spawner_id=RESIDENT, session_id="s-1"
                )
        assert exc_info.value.reason == "session_quota"

    async def test_quota_is_isolated_per_session(self, temp_paths):
        registry, _bus, _provider = await _make_registry(temp_paths, session_spawn_quota=1)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(registry, "_start_agent", _patch_start(registry))
            await registry.spawn_contractor(
                prototype=PROTOTYPE, name="一", spawner_id=RESIDENT, session_id="s-1"
            )
            # Same spawner, different session: allowed
            second = await registry.spawn_contractor(
                prototype=PROTOTYPE, name="二", spawner_id=RESIDENT, session_id="s-2"
            )
            assert second.spawn_depth == 1
            # No session context at all: a single shared bucket per spawner.
            # The first context-less spawn fills the bucket...
            await registry.spawn_contractor(
                prototype=PROTOTYPE, name="三", spawner_id=RESIDENT, session_id=None
            )
            # ...and the next one exhausts it
            with pytest.raises(SpawnRejectedError) as exc_info:
                await registry.spawn_contractor(
                    prototype=PROTOTYPE, name="四", spawner_id=RESIDENT, session_id=None
                )
            assert exc_info.value.reason == "session_quota"


class TestStopContractor:
    async def _spawn(self, registry: AgentRegistry, name: str = "临时工甲") -> AgentConfig:
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(registry, "_start_agent", _patch_start(registry))
            return await registry.spawn_contractor(
                prototype=PROTOTYPE, name=name, spawner_id=RESIDENT, session_id=None
            )

    async def test_stop_discards_contractor_and_deletes_home(self, temp_paths):
        registry, bus, provider = await _make_registry(temp_paths)
        config = await self._spawn(registry)
        home = temp_paths.agent_dir(config.id)
        assert home.exists()

        result = await registry.stop_contractor(config.id, stopped_by=RESIDENT)

        assert result is True
        assert config.id not in registry._agents
        assert config.id not in registry._registered_ids
        assert provider.get_config(config.id) is None
        assert not home.exists()
        stopped = _published(bus, EventType.AGENT_STOPPED)
        assert len(stopped) == 1
        assert stopped[0].payload["agent_id"] == config.id
        assert stopped[0].payload["stopped_by"] == RESIDENT

    async def test_stop_with_live_sessions_is_refused(self, temp_paths):
        registry, _bus, _provider = await _make_registry(temp_paths)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(registry, "_start_agent", _patch_start(registry, active_sessions=1))
            config = await registry.spawn_contractor(
                prototype=PROTOTYPE, name="甲", spawner_id=RESIDENT, session_id=None
            )
        home = temp_paths.agent_dir(config.id)

        with pytest.raises(StopRejectedError) as exc_info:
            await registry.stop_contractor(config.id, stopped_by=RESIDENT)

        assert exc_info.value.reason == "active_sessions"
        assert config.id in registry._agents
        assert home.exists()

    async def test_stop_resident_is_refused(self, temp_paths):
        resident = AgentConfig(id=RESIDENT, name="居民甲")
        registry, _bus, provider = await _make_registry(temp_paths, {RESIDENT: resident})

        with pytest.raises(StopRejectedError) as exc_info:
            await registry.stop_contractor(RESIDENT, stopped_by=RESIDENT)

        assert exc_info.value.reason == "resident_not_stoppable"
        assert provider.get_config(RESIDENT) is not None

    async def test_stop_unknown_agent_is_refused(self, temp_paths):
        registry, _bus, _provider = await _make_registry(temp_paths)
        with pytest.raises(StopRejectedError) as exc_info:
            await registry.stop_contractor("c-ghost-00000000", stopped_by=RESIDENT)
        assert exc_info.value.reason == "unknown_agent"

    async def test_orphaned_contractor_survives_spawner(self, temp_paths):
        """Reparent, not kill: when the spawner goes away (sleeps/stops),
        its contractors keep running independently."""
        spawner = AgentConfig(id=RESIDENT, name="居民甲")
        registry, _bus, provider = await _make_registry(temp_paths, {RESIDENT: spawner})
        config = await self._spawn(registry)
        registry._agents[RESIDENT] = _make_running_agent()

        # Spawner stops (its own lifecycle; nothing cascades)
        spawner_agent = registry._agents.pop(RESIDENT)
        await spawner_agent.shutdown()

        assert config.id in registry._agents
        assert provider.get_config(config.id) is not None
        assert registry.get_agent_status(config.id)["state"] == "active"

    async def test_path_injection_guard(self, temp_paths):
        registry, _bus, _provider = await _make_registry(temp_paths)
        with pytest.raises(StopRejectedError) as exc_info:
            registry._assert_deletable_agent_id("../shared")
        assert exc_info.value.reason == "invalid_agent_id"
        with pytest.raises(StopRejectedError):
            registry._assert_deletable_agent_id("a/b")


class TestIdleReapContractor:
    async def test_idle_contractor_is_discarded_with_home_deleted(self, temp_paths):
        registry, bus, provider = await _make_registry(temp_paths)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(registry, "_start_agent", _patch_start(registry, idle_seconds=9999.0))
            config = await registry.spawn_contractor(
                prototype=PROTOTYPE, name="甲", spawner_id=RESIDENT, session_id=None
            )
        home = temp_paths.agent_dir(config.id)
        agent = registry._agents[config.id]

        await registry._drain_agent(config.id, "drain")

        assert config.id not in registry._agents
        assert config.id not in registry._registered_ids
        assert provider.get_config(config.id) is None
        assert not home.exists()
        agent.shutdown.assert_awaited_once()
        # Discarded, not slept
        assert _published(bus, EventType.AGENT_SLEEPING) == []
        assert len(_published(bus, EventType.AGENT_STOPPED)) == 1

    async def test_idle_resident_sleeps_and_keeps_home(self, temp_paths):
        resident = AgentConfig(
            id=RESIDENT, name="居民甲", activation="lazy", idle_timeout_seconds=600
        )
        registry, bus, provider = await _make_registry(temp_paths, {RESIDENT: resident})
        home = temp_paths.agent_dir(RESIDENT)
        agent = _make_running_agent(active_sessions=0, idle_seconds=9999.0)
        registry._agents[RESIDENT] = agent

        await registry._drain_agent(RESIDENT, "drain")

        assert RESIDENT not in registry._agents
        assert RESIDENT in registry._registered_ids
        assert provider.get_config(RESIDENT) is not None
        assert home.exists()  # resident home is never deleted
        assert len(_published(bus, EventType.AGENT_SLEEPING)) == 1
        assert _published(bus, EventType.AGENT_STOPPED) == []


class TestTools:
    async def _registry_with_spawn(self, temp_paths: TlonPaths, **budget):
        registry, bus, provider = await _make_registry(temp_paths, **budget)
        return registry, bus, provider

    async def test_spawn_agent_tool_success(self, temp_paths):
        registry, _bus, _provider = await self._registry_with_spawn(temp_paths)
        tool = SpawnAgentTool(registry=registry, spawner_id=RESIDENT)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(registry, "_start_agent", _patch_start(registry))
            # Session attribution comes from structlog contextvars
            with bound_contextvars(session_id="sess-42"):
                result = await tool.execute(prototype=PROTOTYPE, name="临时工甲")

        assert result.success is True
        assert result.data["role"] == "contractor"
        assert result.data["agent_id"].startswith(f"c-{PROTOTYPE}-")
        assert registry._spawn_quota[(RESIDENT, "sess-42")] == 1

    async def test_spawn_agent_tool_budget_error_shape(self, temp_paths):
        registry, _bus, _provider = await self._registry_with_spawn(
            temp_paths, session_spawn_quota=0
        )
        tool = SpawnAgentTool(registry=registry, spawner_id=RESIDENT)

        result = await tool.execute(prototype=PROTOTYPE, name="甲")

        assert result.success is False
        assert result.error == "budget_exceeded: session_quota"
        assert result.data["reason"] == "session_quota"

    async def test_spawn_agent_tool_unknown_prototype(self, temp_paths):
        registry, _bus, _provider = await self._registry_with_spawn(temp_paths)
        tool = SpawnAgentTool(registry=registry, spawner_id=RESIDENT)

        result = await tool.execute(prototype="ghost", name="甲")

        assert result.success is False
        assert "ghost" in result.error

    async def test_spawn_tool_schemas(self):
        tool = SpawnAgentTool(registry=MagicMock(), spawner_id=RESIDENT)
        definition = tool.to_openai_definition()
        assert definition["function"]["name"] == "spawnAgent"
        assert definition["function"]["parameters"]["required"] == ["prototype", "name"]

        stop = StopAgentTool(registry=MagicMock(), spawner_id=RESIDENT)
        stop_def = stop.to_openai_definition()
        assert stop_def["function"]["name"] == "stopAgent"
        assert stop_def["function"]["parameters"]["required"] == ["agent_id"]

        list_tool = ListAgentsTool(registry=MagicMock())
        assert list_tool.to_openai_definition()["function"]["name"] == "listAgents"

    async def test_stop_agent_tool_resident_error(self, temp_paths):
        resident = AgentConfig(id=RESIDENT, name="居民甲")
        registry, _bus, _provider = await _make_registry(temp_paths, {RESIDENT: resident})
        tool = StopAgentTool(registry=registry, spawner_id=RESIDENT)

        result = await tool.execute(agent_id=RESIDENT)

        assert result.success is False
        assert result.error == "stop_rejected: resident_not_stoppable"

    async def test_list_agents_tool_reports_identity(self, temp_paths):
        resident = AgentConfig(id=RESIDENT, name="居民甲")
        registry, _bus, _provider = await _make_registry(temp_paths, {RESIDENT: resident})
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(registry, "_start_agent", _patch_start(registry))
            spawned = await registry.spawn_contractor(
                prototype=PROTOTYPE, name="临时工甲", spawner_id=RESIDENT, session_id=None
            )

        tool = ListAgentsTool(registry=registry)
        result = await tool.execute()

        assert result.success is True
        by_id = {entry["id"]: entry for entry in result.data}
        assert by_id[RESIDENT]["role"] == "resident"
        contractor = by_id[spawned.id]
        assert contractor["role"] == "contractor"
        assert contractor["prototype"] == PROTOTYPE
        assert contractor["spawned_by"] == RESIDENT
        assert contractor["state"] == "active"

    def test_ednify_keys_converts_nested_string_keys(self):
        data = {"loop": {"max-iterations": 5}, "plain": [1, {"a": 2}]}
        result = _ednify_keys(data)
        assert result[Keyword("loop")][Keyword("max-iterations")] == 5
        assert result[Keyword("plain")][1][Keyword("a")] == 2
        assert all(isinstance(k, Keyword) for k in result)


class TestQuotaResetOnShutdown:
    async def test_shutdown_clears_quota_ledger(self, temp_paths):
        registry, _bus, _provider = await _make_registry(temp_paths)
        registry._spawn_quota[(RESIDENT, "s-1")] = 2
        await registry.shutdown()
        assert registry._spawn_quota == {}


class TestWakeLockSerialization:
    async def test_stop_rechecks_sessions_under_lock(self, temp_paths):
        """A session starting in the race window (stop waits on the
        per-agent lock) keeps the contractor alive."""
        registry, _bus, _provider = await _make_registry(temp_paths)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(registry, "_start_agent", _patch_start(registry))
            config = await registry.spawn_contractor(
                prototype=PROTOTYPE, name="甲", spawner_id=RESIDENT, session_id=None
            )
        agent = registry._agents[config.id]

        lock = registry._wake_locks.setdefault(config.id, asyncio.Lock())
        await lock.acquire()
        stop_task = asyncio.create_task(
            registry.stop_contractor(config.id, stopped_by=RESIDENT)
        )
        await asyncio.sleep(0.05)  # stop passed first check, waits on lock

        agent.active_session_count = 1  # session starts in the race window
        lock.release()
        with pytest.raises(StopRejectedError) as exc_info:
            await stop_task

        assert exc_info.value.reason == "active_sessions"
        assert config.id in registry._agents
