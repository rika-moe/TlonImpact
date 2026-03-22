"""Tests for ConfigManager."""

from unittest.mock import Mock

import pytest

from tlon.app.server.config_manager import ConfigManager
from tlon.base.config.loader import ConfigLoader
from tlon.base.config.models import AgentConfig


class TestConfigManagerInit:
    """Test ConfigManager initialization."""

    def test_init_with_loader(self):
        """Test that ConfigManager initializes with a loader."""
        loader = Mock(spec=ConfigLoader)
        manager = ConfigManager(loader)

        assert manager._loader is loader
        assert manager._agents == {}


class TestInitialLoad:
    """Test initial configuration loading."""

    @pytest.mark.asyncio
    async def test_initialize_loads_agents(self):
        """Test that initialize loads agents."""
        # Setup mock loader
        loader = Mock(spec=ConfigLoader)
        loader.load_all_agents.return_value = {
            "agent-1": AgentConfig(id="agent-1", name="Test Agent"),
        }

        manager = ConfigManager(loader)
        await manager.initialize()

        assert len(manager.agents) == 1
        assert manager.get_agent("agent-1") is not None

    @pytest.mark.asyncio
    async def test_initialize_empty(self):
        """Test that initialize handles empty configuration."""
        loader = Mock(spec=ConfigLoader)
        loader.load_all_agents.return_value = {}

        manager = ConfigManager(loader)
        await manager.initialize()

        assert manager.agents == {}


class TestQueryInterface:
    """Test configuration query methods."""

    def test_agents_returns_copy(self):
        """Test that agents property returns a copy."""
        loader = Mock(spec=ConfigLoader)
        manager = ConfigManager(loader)
        manager._agents = {"agent-1": Mock()}

        agents = manager.agents
        agents["agent-2"] = Mock()

        # Original should be unchanged
        assert "agent-2" not in manager._agents

    def test_get_agent_existing(self):
        """Test getting an existing agent."""
        loader = Mock(spec=ConfigLoader)
        manager = ConfigManager(loader)

        agent = AgentConfig(id="agent-1", name="Agent 1")
        manager._agents = {"agent-1": agent}

        result = manager.get_agent("agent-1")
        assert result is agent

    def test_get_agent_nonexistent(self):
        """Test getting a non-existent agent."""
        loader = Mock(spec=ConfigLoader)
        manager = ConfigManager(loader)

        result = manager.get_agent("nonexistent")
        assert result is None
