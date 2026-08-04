"""Tests for AgentRegistry.adopt_agent (P3: hot adoption of agents created
out-of-process by wizard/mirror).

Covers all five result branches:
- "started": new non-lazy agent is reloaded, registered and started
- "registered-lazy": new lazy agent is registered but not started
- "already-known": running or registered agent is a no-op (no reload)
- "config-not-found": provider has no agent.yaml for the id
- "start-failed": assembly fails (e.g. behavior file missing/invalid)
"""

import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.agents.agent_registry import AgentRegistry
from tlon.base.config import AgentConfig
from tlon.base.paths import TlonPaths


class MockAgentInfoProvider:
    """Mock AgentInfoProvider with a stubbable reload_agent."""

    def __init__(self, configs: dict[str, AgentConfig], reload_ok: bool = True):
        self._configs = configs
        self.reload_agent = AsyncMock(return_value=reload_ok)

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)

    def get_all_ids(self) -> set[str]:
        return set(self._configs.keys())


def _make_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock()
    bus.subscribe = MagicMock(side_effect=["sub-start", "sub-stop"])
    bus.unsubscribe = MagicMock()
    return bus


@pytest.fixture
def temp_paths():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield TlonPaths(home=tmpdir)


def _make_registry(
    configs: dict[str, AgentConfig],
    temp_paths: TlonPaths,
    reload_ok: bool = True,
) -> tuple[AgentRegistry, MockAgentInfoProvider]:
    provider = MockAgentInfoProvider(configs, reload_ok=reload_ok)
    registry = AgentRegistry(
        event_bus=_make_bus(),
        agent_info_provider=provider,
        paths=temp_paths,
    )
    return registry, provider


class TestAdoptAgent:
    """AgentRegistry.adopt_agent result branches."""

    @pytest.mark.asyncio
    async def test_new_always_agent_is_started(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="always")}
        registry, provider = _make_registry(configs, temp_paths)
        started_agent = MagicMock()

        with patch.object(
            registry, "_start_agent", new=AsyncMock(return_value=started_agent)
        ) as mock_start:
            result = await registry.adopt_agent("a-001")

        assert result == "started"
        provider.reload_agent.assert_awaited_once_with("a-001")
        mock_start.assert_awaited_once_with("a-001")
        assert "a-001" in registry._registered_ids

    @pytest.mark.asyncio
    async def test_new_lazy_agent_is_registered_not_started(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="lazy")}
        registry, _provider = _make_registry(configs, temp_paths)

        with patch.object(registry, "_start_agent", new=AsyncMock()) as mock_start:
            result = await registry.adopt_agent("a-001")

        assert result == "registered-lazy"
        mock_start.assert_not_called()
        assert "a-001" in registry._registered_ids
        assert registry.get_agent("a-001") is None

    @pytest.mark.asyncio
    async def test_running_agent_is_already_known(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="always")}
        registry, provider = _make_registry(configs, temp_paths)
        registry._agents["a-001"] = MagicMock()

        with patch.object(registry, "_start_agent", new=AsyncMock()) as mock_start:
            result = await registry.adopt_agent("a-001")

        assert result == "already-known"
        provider.reload_agent.assert_not_called()
        mock_start.assert_not_called()

    @pytest.mark.asyncio
    async def test_registered_sleeping_agent_is_already_known(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="lazy")}
        registry, provider = _make_registry(configs, temp_paths)
        registry.register("a-001")

        with patch.object(registry, "_start_agent", new=AsyncMock()) as mock_start:
            result = await registry.adopt_agent("a-001")

        assert result == "already-known"
        provider.reload_agent.assert_not_called()
        mock_start.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_config_is_config_not_found(self, temp_paths):
        registry, provider = _make_registry({}, temp_paths, reload_ok=False)

        with patch.object(registry, "_start_agent", new=AsyncMock()) as mock_start:
            result = await registry.adopt_agent("a-404")

        assert result == "config-not-found"
        provider.reload_agent.assert_awaited_once_with("a-404")
        mock_start.assert_not_called()
        assert "a-404" not in registry._registered_ids

    @pytest.mark.asyncio
    async def test_failed_assembly_is_start_failed(self, temp_paths):
        """e.g. agent.yaml reloaded but the behavior file is missing/invalid."""
        configs = {"a-001": AgentConfig(id="a-001", name="甲", activation="always")}
        registry, _provider = _make_registry(configs, temp_paths)

        with patch.object(registry, "_start_agent", new=AsyncMock(return_value=None)):
            result = await registry.adopt_agent("a-001")

        assert result == "start-failed"
        assert "a-001" in registry._registered_ids
