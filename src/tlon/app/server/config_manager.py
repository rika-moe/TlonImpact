"""Configuration management for Tlon Server with hot-reload support.

Provides ConfigManager which loads configuration from files,
maintains cached state, and notifies subscribers of changes.
"""

from typing import Any, Callable, Dict, List, Optional

from tlon.base.config import ConfigLoader, PositionConfig, AgentConfig
from tlon.base.logger import get_logger

logger = get_logger(__name__)


class ConfigManager:
    """Manages configuration loading and hot-reload.
    
    This class is responsible for:
    - Loading configuration from files
    - Maintaining cached configuration state
    - Notifying subscribers of configuration changes
    - Supporting runtime hot-reload of configuration
    
    Example:
        >>> config_manager = ConfigManager(ConfigLoader())
        >>> await config_manager.initial_load()
        >>> config_manager.on_agents_changed(my_callback)
        >>> await config_manager.reload_agents()  # Triggers callback
    """
    
    def __init__(self, config_loader: ConfigLoader):
        """Initialize the configuration manager.
        
        Args:
            config_loader: The ConfigLoader instance to use for loading
        """
        self._loader = config_loader
        self._positions: Dict[str, PositionConfig] = {}
        self._agents: Dict[str, AgentConfig] = {}
        
        # Callbacks for configuration changes
        self._on_positions_changed: List[Callable[[Dict[str, PositionConfig]], None]] = []
        self._on_agents_changed: List[Callable[[Dict[str, AgentConfig]], None]] = []
    
    async def initial_load(self) -> None:
        """Load initial configuration.
        
        This should be called once during server initialization.
        Loads all positions and agents from configuration files.
        """
        logger.info("Loading initial configuration...")
        
        self._positions = self._loader.load_all_positions()
        self._agents = self._loader.load_all_agents()
        
        logger.info(f"Loaded {len(self._positions)} positions and {len(self._agents)} agents")
    
    # ========== Subscription Interface ==========
    
    def on_positions_changed(self, callback: Callable[[Dict[str, PositionConfig]], None]) -> None:
        """Register a callback for position configuration changes.
        
        The callback will be invoked when reload_positions() is called.
        
        Args:
            callback: Function to call with new positions dict
        """
        self._on_positions_changed.append(callback)
        logger.debug(f"Registered positions change callback: {callback}")
    
    def on_agents_changed(self, callback: Callable[[Dict[str, AgentConfig]], None]) -> None:
        """Register a callback for agent configuration changes.
        
        The callback will be invoked when reload_agents() is called.
        
        Args:
            callback: Function to call with new agents dict
        """
        self._on_agents_changed.append(callback)
        logger.debug(f"Registered agents change callback: {callback}")
    
    # ========== Reload Interface ==========
    
    async def reload_positions(self) -> bool:
        """Reload position configuration from files.
        
        Forces a reload from disk and notifies all registered callbacks.
        
        Returns:
            True if reload was successful
            
        Raises:
            ConfigLoadError: If loading fails (propagated from ConfigLoader)
        """
        logger.info("Reloading position configuration...")
        
        try:
            new_positions = self._loader.load_all_positions(force_reload=True)
            self._positions = new_positions
            
            # Notify all subscribers
            for callback in self._on_positions_changed:
                try:
                    callback(new_positions)
                except Exception as e:
                    logger.error(f"Error in positions change callback: {e}")
            
            logger.info(f"Reloaded {len(new_positions)} positions")
            return True
            
        except Exception as e:
            logger.error(f"Failed to reload positions: {e}")
            raise
    
    async def reload_agents(self) -> bool:
        """Reload agent configuration from files.
        
        Forces a reload from disk and notifies all registered callbacks.
        
        Returns:
            True if reload was successful
            
        Raises:
            ConfigLoadError: If loading fails (propagated from ConfigLoader)
        """
        logger.info("Reloading agent configuration...")
        
        try:
            new_agents = self._loader.load_all_agents(force_reload=True)
            self._agents = new_agents
            
            # Notify all subscribers
            for callback in self._on_agents_changed:
                try:
                    callback(new_agents)
                except Exception as e:
                    logger.error(f"Error in agents change callback: {e}")
            
            logger.info(f"Reloaded {len(new_agents)} agents")
            return True
            
        except Exception as e:
            logger.error(f"Failed to reload agents: {e}")
            raise
    
    # ========== Query Interface ==========
    
    @property
    def positions(self) -> Dict[str, PositionConfig]:
        """Get current position configurations.
        
        Returns:
            Dictionary mapping position type to PositionConfig
        """
        return self._positions.copy()
    
    @property
    def agents(self) -> Dict[str, AgentConfig]:
        """Get current agent configurations.
        
        Returns:
            Dictionary mapping agent ID to AgentConfig
        """
        return self._agents.copy()
    
    def get_position(self, position_type: str) -> Optional[PositionConfig]:
        """Get a specific position by type.
        
        Args:
            position_type: The position type identifier
            
        Returns:
            PositionConfig or None if not found
        """
        return self._positions.get(position_type)
    
    def get_agent(self, agent_id: str) -> Optional[AgentConfig]:
        """Get a specific agent by ID.
        
        Args:
            agent_id: The agent identifier
            
        Returns:
            AgentConfig or None if not found
        """
        return self._agents.get(agent_id)
