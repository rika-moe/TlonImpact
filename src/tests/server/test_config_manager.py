"""Tests for ConfigManager."""

from unittest.mock import Mock

import pytest

from tlon.app.server.config_manager import ConfigManager
from tlon.base.config.loader import ConfigLoader
from tlon.base.config.models import AgentConfig, PositionConfig


class TestConfigManagerInit:
    """Test ConfigManager initialization."""

    def test_init_with_loader(self):
        """Test that ConfigManager initializes with a loader."""
        loader = Mock(spec=ConfigLoader)
        manager = ConfigManager(loader)

        assert manager._loader is loader
        assert manager._positions == {}
        assert manager._agents == {}


class TestInitialLoad:
    """Test initial configuration loading."""

    @pytest.mark.asyncio
    async def test_initial_load_positions_and_agents(self):
        """Test that initial_load loads both positions and agents."""
        # Setup mock loader
        loader = Mock(spec=ConfigLoader)
        loader.load_all_positions.return_value = {
            "dev": PositionConfig(id="dev", type="developer", title="Developer"),
        }
        loader.load_all_agents.return_value = {
            "agent-1": AgentConfig(id="agent-1", name="Test Agent", position_id="dev"),
        }

        manager = ConfigManager(loader)
        await manager.initial_load()

        assert len(manager.positions) == 1
        assert len(manager.agents) == 1
        assert manager.get_position("dev") is not None
        assert manager.get_agent("agent-1") is not None

    @pytest.mark.asyncio
    async def test_initial_load_empty(self):
        """Test that initial_load handles empty configuration."""
        loader = Mock(spec=ConfigLoader)
        loader.load_all_positions.return_value = {}
        loader.load_all_agents.return_value = {}

        manager = ConfigManager(loader)
        await manager.initial_load()

        assert manager.positions == {}
        assert manager.agents == {}


class TestQueryInterface:
    """Test configuration query methods."""

    def test_positions_returns_copy(self):
        """Test that positions property returns a copy."""
        loader = Mock(spec=ConfigLoader)
        manager = ConfigManager(loader)
        manager._positions = {"dev": Mock()}

        positions = manager.positions
        positions["new"] = Mock()

        # Original should be unchanged
        assert "new" not in manager._positions

    def test_agents_returns_copy(self):
        """Test that agents property returns a copy."""
        loader = Mock(spec=ConfigLoader)
        manager = ConfigManager(loader)
        manager._agents = {"agent-1": Mock()}

        agents = manager.agents
        agents["agent-2"] = Mock()

        # Original should be unchanged
        assert "agent-2" not in manager._agents

    def test_get_position_existing(self):
        """Test getting an existing position."""
        loader = Mock(spec=ConfigLoader)
        manager = ConfigManager(loader)

        position = PositionConfig(id="dev", type="developer", title="Developer")
        manager._positions = {"developer": position}

        result = manager.get_position("developer")
        assert result is position

    def test_get_position_nonexistent(self):
        """Test getting a non-existent position."""
        loader = Mock(spec=ConfigLoader)
        manager = ConfigManager(loader)

        result = manager.get_position("nonexistent")
        assert result is None

    def test_get_agent_existing(self):
        """Test getting an existing agent."""
        loader = Mock(spec=ConfigLoader)
        manager = ConfigManager(loader)

        agent = AgentConfig(id="agent-1", name="Agent 1", position_id="dev")
        manager._agents = {"agent-1": agent}

        result = manager.get_agent("agent-1")
        assert result is agent

    def test_get_agent_nonexistent(self):
        """Test getting a non-existent agent."""
        loader = Mock(spec=ConfigLoader)
        manager = ConfigManager(loader)

        result = manager.get_agent("nonexistent")
        assert result is None
