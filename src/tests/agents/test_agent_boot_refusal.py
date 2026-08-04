"""Tests for agent boot refusal (total behavior spec).

A missing or invalid behavior.tlon must refuse only that agent's boot:
``_start_agent`` returns None, logs ``agent_start_failed``, and the
registry keeps serving the other agents without blowing up.
"""

import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.spec import standard_behavior_edn
from tlon.base.config import AgentConfig
from tlon.base.paths import TlonPaths


class MockAgentInfoProvider:
    """Mock AgentInfoProvider for testing."""

    def __init__(self, configs: dict[str, AgentConfig]):
        self._configs = configs

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)

    def get_all_ids(self) -> set[str]:
        return set(self._configs.keys())


class TestAgentBootRefusal:
    @pytest.fixture
    def mock_event_bus(self):
        bus = MagicMock()
        bus.publish = AsyncMock()
        bus.subscribe = MagicMock(return_value="subscription-id")
        bus.unsubscribe = MagicMock()
        return bus

    @pytest.fixture
    def temp_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield TlonPaths(home=tmpdir)

    @pytest.fixture
    def registry(self, mock_event_bus, temp_paths):
        """Two registered agents; only a-good has a behavior file."""
        provider = MockAgentInfoProvider(
            {
                "a-good": AgentConfig(id="a-good", name="林知遥"),
                "a-bad": AgentConfig(id="a-bad", name="残卷"),
            }
        )
        behavior_file = temp_paths.agent_behavior_file("a-good")
        behavior_file.parent.mkdir(parents=True, exist_ok=True)
        behavior_file.write_text(standard_behavior_edn(), encoding="utf-8")
        reg = AgentRegistry(
            event_bus=mock_event_bus,
            agent_info_provider=provider,
            paths=temp_paths,
        )
        reg.register("a-good")
        reg.register("a-bad")
        return reg

    @pytest.fixture
    def mock_infra(self):
        """Mock MemoryManager/TaskManager, as in the assembly tests."""
        with (
            patch("tlon.agents.agent_registry.MemoryManager") as mock_memory_cls,
            patch("tlon.agents.agent_registry.TaskManager") as mock_tm_cls,
        ):
            mock_memory = MagicMock()
            mock_memory.load = AsyncMock()
            mock_memory_cls.return_value = mock_memory
            mock_tm = MagicMock()
            mock_tm.load = MagicMock()
            mock_tm_cls.return_value = mock_tm
            yield

    @pytest.mark.asyncio
    async def test_missing_behavior_file_refuses_only_that_agent(
        self, registry, temp_paths, mock_infra
    ):
        """a-bad (no behavior file) fails to boot; a-good starts normally."""
        with (
            patch("tlon.agents.spec.paths", temp_paths),
            patch("tlon.agents.agent_registry.logger") as mock_logger,
        ):
            bad_agent = await registry._start_agent("a-bad")
            good_agent = await registry._start_agent("a-good")

        assert bad_agent is None
        assert good_agent is not None

        failed_calls = [
            call
            for call in mock_logger.error.call_args_list
            if call.args and call.args[0] == "agent_start_failed"
        ]
        assert len(failed_calls) == 1
        assert failed_calls[0].kwargs["agent_id"] == "a-bad"

        # Registry is intact: only the good agent is tracked as active.
        assert registry.get_agent("a-good") is good_agent
        assert registry.get_agent("a-bad") is None

    @pytest.mark.asyncio
    async def test_invalid_behavior_file_refuses_only_that_agent(
        self, registry, temp_paths, mock_infra
    ):
        """a-bad (incomplete spec) fails to boot; a-good starts normally."""
        behavior_file = temp_paths.agent_behavior_file("a-bad")
        behavior_file.parent.mkdir(parents=True, exist_ok=True)
        behavior_file.write_text("{:version 1}", encoding="utf-8")

        with (
            patch("tlon.agents.spec.paths", temp_paths),
            patch("tlon.agents.agent_registry.logger") as mock_logger,
        ):
            bad_agent = await registry._start_agent("a-bad")
            good_agent = await registry._start_agent("a-good")

        assert bad_agent is None
        assert good_agent is not None

        failed_calls = [
            call
            for call in mock_logger.error.call_args_list
            if call.args and call.args[0] == "agent_start_failed"
        ]
        assert len(failed_calls) == 1
        assert failed_calls[0].kwargs["agent_id"] == "a-bad"

        assert registry.get_agent("a-good") is good_agent
        assert registry.get_agent("a-bad") is None

    @pytest.mark.asyncio
    async def test_registry_still_starts_agents_after_a_refusal(
        self, registry, temp_paths, mock_infra
    ):
        """A failed boot does not wedge the registry for later starts."""
        with patch("tlon.agents.spec.paths", temp_paths):
            assert await registry._start_agent("a-bad") is None
            good_agent = await registry._start_agent("a-good")

        assert good_agent is not None
        assert registry.get_agent("a-good") is good_agent
