"""Configuration management for Tlon Server.

Provides ConfigManager which loads configuration from files,
maintains cached state, and notifies subscribers of changes.
"""

from typing import Dict, Optional

from tlon.base.config import ConfigLoader, PositionConfig, AgentConfig
from tlon.base.logger import get_logger

logger = get_logger(__name__)


class ConfigManager:
    """Manages configuration loading.
    
    This class is responsible for:
    - Loading configuration from files
    - Maintaining cached configuration state
    - Notifying subscribers of configuration changes

    Example:
        >>> config_manager = ConfigManager(ConfigLoader())
        >>> await config_manager.initial_load()
        >>> config_manager.on_agents_changed(my_callback)
    """
    
    def __init__(self, config_loader: ConfigLoader):
        """Initialize the configuration manager.
        
        Args:
            config_loader: The ConfigLoader instance to use for loading
        """
        self._loader = config_loader
        self._positions: Dict[str, PositionConfig] = {}
        self._agents: Dict[str, AgentConfig] = {}

    async def initial_load(self) -> None:
        """Load initial configuration.
        
        This should be called once during server initialization.
        Loads all positions and agents from configuration files.
        """
        logger.info("Loading initial configuration...")
        
        self._positions = self._loader.load_all_positions()
        self._agents = self._loader.load_all_agents()
        
        logger.info(f"Loaded {len(self._positions)} positions and {len(self._agents)} agents")

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
