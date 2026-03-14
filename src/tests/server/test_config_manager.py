"""Tests for ConfigManager."""

import pytest
from unittest.mock import Mock, MagicMock

from tlon.app.server.config_manager import ConfigManager
from tlon.base.config.loader import ConfigLoader
from tlon.base.config.models import PositionConfig, AgentConfig, LLMConfig, DockerRuntimeConfig


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


class TestSubscription:
    """Test callback subscription mechanism."""
    
    def test_on_positions_changed_registration(self):
        """Test registering positions change callback."""
        loader = Mock(spec=ConfigLoader)
        manager = ConfigManager(loader)
        
        callback = Mock()
        manager.on_positions_changed(callback)
        
        assert callback in manager._on_positions_changed
    
    def test_on_agents_changed_registration(self):
        """Test registering agents change callback."""
        loader = Mock(spec=ConfigLoader)
        manager = ConfigManager(loader)
        
        callback = Mock()
        manager.on_agents_changed(callback)
        
        assert callback in manager._on_agents_changed
    
    def test_multiple_callbacks(self):
        """Test registering multiple callbacks."""
        loader = Mock(spec=ConfigLoader)
        manager = ConfigManager(loader)
        
        callback1 = Mock()
        callback2 = Mock()
        
        manager.on_positions_changed(callback1)
        manager.on_positions_changed(callback2)
        
        assert len(manager._on_positions_changed) == 2


class TestReloadPositions:
    """Test position configuration reloading."""
    
    @pytest.mark.asyncio
    async def test_reload_positions_triggers_callback(self):
        """Test that reload triggers registered callbacks."""
        # Setup
        loader = Mock(spec=ConfigLoader)
        loader.load_all_positions.return_value = {
            "dev": PositionConfig(id="dev", type="developer", title="Developer"),
        }
        loader.load_all_agents.return_value = {}  # Need to set this too
        
        manager = ConfigManager(loader)
        await manager.initial_load()
        
        # Register callback
        callback = Mock()
        manager.on_positions_changed(callback)
        
        # Reload with new data
        loader.load_all_positions.return_value = {
            "dev": PositionConfig(id="dev", type="developer", title="Developer"),
            "pm": PositionConfig(id="pm", type="pm", title="Product Manager"),
        }
        
        await manager.reload_positions()
        
        # Verify callback was called with new data
        callback.assert_called_once()
        args = callback.call_args[0][0]
        assert len(args) == 2
        assert "pm" in args
    
    @pytest.mark.asyncio
    async def test_reload_positions_updates_cache(self):
        """Test that reload updates internal cache."""
        loader = Mock(spec=ConfigLoader)
        old_position = PositionConfig(id="old", type="old", title="Old")
        loader.load_all_positions.return_value = {"old": old_position}
        loader.load_all_agents.return_value = {}  # Need to set this too
        
        manager = ConfigManager(loader)
        await manager.initial_load()
        
        new_position = PositionConfig(id="new", type="new", title="New")
        new_positions = {"new": new_position}
        loader.load_all_positions.return_value = new_positions
        
        await manager.reload_positions()
        
        assert manager.positions == new_positions
    
    @pytest.mark.asyncio
    async def test_reload_positions_force_reload(self):
        """Test that reload uses force_reload=True."""
        loader = Mock(spec=ConfigLoader)
        loader.load_all_positions.return_value = {}
        
        manager = ConfigManager(loader)
        await manager.reload_positions()
        
        loader.load_all_positions.assert_called_with(force_reload=True)


class TestReloadAgents:
    """Test agent configuration reloading."""
    
    @pytest.mark.asyncio
    async def test_reload_agents_triggers_callback(self):
        """Test that reload triggers registered callbacks."""
        # Setup
        loader = Mock(spec=ConfigLoader)
        loader.load_all_positions.return_value = {}  # Need to set this too
        loader.load_all_agents.return_value = {
            "agent-1": AgentConfig(id="agent-1", name="Agent 1", position_id="dev"),
        }
        
        manager = ConfigManager(loader)
        await manager.initial_load()
        
        # Register callback
        callback = Mock()
        manager.on_agents_changed(callback)
        
        # Reload with new data
        loader.load_all_agents.return_value = {
            "agent-1": AgentConfig(id="agent-1", name="Agent 1", position_id="dev"),
            "agent-2": AgentConfig(id="agent-2", name="Agent 2", position_id="dev"),
        }
        
        await manager.reload_agents()
        
        # Verify callback was called
        callback.assert_called_once()
        args = callback.call_args[0][0]
        assert len(args) == 2
    
    @pytest.mark.asyncio
    async def test_reload_agents_handles_callback_error(self):
        """Test that reload continues even if a callback fails."""
        loader = Mock(spec=ConfigLoader)
        loader.load_all_agents.return_value = {}
        
        manager = ConfigManager(loader)
        
        # Register one failing and one succeeding callback
        failing_callback = Mock(side_effect=Exception("Callback error"))
        success_callback = Mock()
        
        manager.on_agents_changed(failing_callback)
        manager.on_agents_changed(success_callback)
        
        # Should not raise
        await manager.reload_agents()
        
        # Both should be called
        failing_callback.assert_called_once()
        success_callback.assert_called_once()


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
