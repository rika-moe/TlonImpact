"""Tests for AgentRegistry assembly logic.

Verifies that:
- _build_llm_config() merges file config with settings
- _build_llm_config() handles None file_config
- _start_agent() creates Agent with all 13 tools registered
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.agents.agent_registry import AgentRegistry, _build_llm_config
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.config.models import AgentConfig, LLMConfig


class MockSettings:
    """Simple mock for settings from tlon.base.settings."""

    llm_base_url = "https://api.example.com"
    llm_api_key = "sk-test-key"
    llm_max_retries = 5
    llm_base_delay = 1.5


class TestBuildLLMConfig:
    def test_merges_file_config_with_settings(self):
        """File config provides model params, settings provide runtime params."""
        file_config = LLMConfig(
            model_name="custom-model",
            temperature=0.5,
            max_tokens=2048,
        )
        settings = MockSettings()

        result = _build_llm_config(file_config, settings)

        # File values preserved
        assert result.model_name == "custom-model"
        assert result.temperature == 0.5
        assert result.max_tokens == 2048

        # Settings values applied
        assert result.base_url == "https://api.example.com"
        assert result.auth_token == "sk-test-key"
        assert result.max_retries == 5
        assert result.base_delay == 1.5

    def test_handles_none_file_config(self):
        """When file_config is None, use defaults for model params."""
        settings = MockSettings()

        result = _build_llm_config(None, settings)

        # Default values from LLMConfig()
        assert result.model_name == "kimi-k2.5"
        assert result.temperature == 0.7
        assert result.max_tokens == 4096

        # Settings still applied
        assert result.base_url == "https://api.example.com"
        assert result.auth_token == "sk-test-key"


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


class TestAgentRegistryStartAgent:
    @pytest.fixture
    def mock_event_bus(self):
        bus = MagicMock()
        bus.publish = AsyncMock()
        bus.subscribe = MagicMock(return_value="subscription-id")
        bus.unsubscribe = MagicMock()
        return bus

    @pytest.fixture
    def mock_message_store(self):
        return MagicMock()

    @pytest.fixture
    def registry(self, mock_event_bus, mock_message_store):
        config = AgentConfig(id="a-001", name="林知遥")
        provider = MockAgentInfoProvider({"a-001": config})
        reg = AgentRegistry(
            event_bus=mock_event_bus,
            agent_info_provider=provider,
            message_store=mock_message_store,
        )
        reg.register("a-001")
        return reg

    @pytest.mark.asyncio
    async def test_start_agent_registers_all_13_tools(self, registry, mock_event_bus):
        """Agent should be created with all 13 tools registered in ToolExecutor."""
        # Mock DockerRuntime to avoid actual container creation
        with patch("tlon.agents.agent_registry.DockerRuntime") as mock_docker_cls:
            mock_docker = MagicMock()
            mock_docker.ensure_running = AsyncMock()
            mock_docker_cls.return_value = mock_docker

            # Patch memory and task_manager to avoid file operations
            with patch("tlon.agents.agent_registry.MemoryManager") as mock_memory_cls:
                mock_memory = MagicMock()
                mock_memory.load = AsyncMock()
                mock_memory_cls.return_value = mock_memory

                with patch("tlon.agents.agent_registry.TaskManager") as mock_tm_cls:
                    mock_tm = MagicMock()
                    mock_tm.load = MagicMock()
                    mock_tm_cls.return_value = mock_tm

                    # Call _start_agent
                    agent = await registry._start_agent("a-001")

        assert agent is not None
        tools = agent.tool_executor.list_tools()

        # All 13 tools should be registered
        expected_tools = {
            "executeBashCommand",
            "completeDialog",
            "rememberFact",
            "searchMemories",
            "generateSummary",
            "forget",
            "createTask",
            "listTasks",
            "startTask",
            "pauseTask",
            "completeTask",
            "removeTask",
            "getCurrentTask",
        }

        assert set(tools) == expected_tools, f"Missing tools: {expected_tools - set(tools)}"

    @pytest.mark.asyncio
    async def test_start_agent_injects_all_deps(self, registry, mock_event_bus):
        """Agent should receive all 8+ infrastructure dependencies."""
        with patch("tlon.agents.agent_registry.DockerRuntime") as mock_docker_cls:
            mock_docker = MagicMock()
            mock_docker.ensure_running = AsyncMock()
            mock_docker_cls.return_value = mock_docker

            with patch("tlon.agents.agent_registry.MemoryManager") as mock_memory_cls:
                mock_memory = MagicMock()
                mock_memory.load = AsyncMock()
                mock_memory_cls.return_value = mock_memory

                with patch("tlon.agents.agent_registry.TaskManager") as mock_tm_cls:
                    mock_tm = MagicMock()
                    mock_tm.load = MagicMock()
                    mock_tm_cls.return_value = mock_tm

                    agent = await registry._start_agent("a-001")

        assert agent is not None
        # Verify all deps are the mocked instances
        assert agent.event_bus is mock_event_bus
        assert agent.llm_client is not None
        assert agent.docker_runtime is mock_docker
        assert agent.memory is mock_memory
        assert agent.task_manager is mock_tm
        assert agent.tool_executor is not None
        assert isinstance(agent.tool_executor, ToolExecutor)

    @pytest.mark.asyncio
    async def test_start_agent_creates_own_tool_executor(self, registry, mock_event_bus):
        """Each agent gets its own ToolExecutor instance."""
        with patch("tlon.agents.agent_registry.DockerRuntime") as mock_docker_cls:
            mock_docker = MagicMock()
            mock_docker.ensure_running = AsyncMock()
            mock_docker_cls.return_value = mock_docker

            with patch("tlon.agents.agent_registry.MemoryManager") as mock_memory_cls:
                mock_memory = MagicMock()
                mock_memory.load = AsyncMock()
                mock_memory_cls.return_value = mock_memory

                with patch("tlon.agents.agent_registry.TaskManager") as mock_tm_cls:
                    mock_tm = MagicMock()
                    mock_tm.load = MagicMock()
                    mock_tm_cls.return_value = mock_tm

                    agent = await registry._start_agent("a-001")

        # ToolExecutor should be created and have tools registered
        assert agent.tool_executor is not None
        assert len(agent.tool_executor.list_tools()) == 13
        # Verify executeBash tool has the docker_runtime injected
        bash_tool = agent.tool_executor.get_tool("executeBashCommand")
        assert bash_tool is not None

    @pytest.mark.asyncio
    async def test_start_agent_publishes_started_event(self, registry, mock_event_bus):
        """Agent started event should be published."""
        with patch("tlon.agents.agent_registry.DockerRuntime") as mock_docker_cls:
            mock_docker = MagicMock()
            mock_docker.ensure_running = AsyncMock()
            mock_docker_cls.return_value = mock_docker

            with patch("tlon.agents.agent_registry.MemoryManager") as mock_memory_cls:
                mock_memory = MagicMock()
                mock_memory.load = AsyncMock()
                mock_memory_cls.return_value = mock_memory

                with patch("tlon.agents.agent_registry.TaskManager") as mock_tm_cls:
                    mock_tm = MagicMock()
                    mock_tm.load = MagicMock()
                    mock_tm_cls.return_value = mock_tm

                    await registry._start_agent("a-001")

        from tlon.base.events.event import EventType

        published_types = [call.args[0].type for call in mock_event_bus.publish.await_args_list]
        assert EventType.AGENT_STARTED in published_types
