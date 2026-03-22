"""Configuration management for Tlon Server.

Provides ConfigManager which loads configuration from files,
maintains cached state, and notifies subscribers of changes.

Architecture:
    ConfigManager is a Component that depends on:
    - ConfigLoader (for file loading)

    It implements AgentInfoProvider interface for agent configuration queries.
"""

from tlon.base.component import Component
from tlon.base.config import AgentConfig, ConfigLoader
from tlon.base.logger import get_logger

logger = get_logger(__name__)


class ConfigManager(Component):
    """Manages configuration loading.

    This class is responsible for:
    - Loading configuration from files
    - Maintaining cached configuration state

    Dependencies (constructor injected):
        - config_loader: ConfigLoader

    Example:
        >>> config_manager = ConfigManager(ConfigLoader())
        >>> await config_manager.initialize()  # loads configuration
    """

    def __init__(self, config_loader: ConfigLoader):
        """Initialize the configuration manager.

        All dependencies are injected via constructor (DAG architecture).

        Args:
            config_loader: The ConfigLoader instance to use for loading
        """
        super().__init__(name="ConfigManager")
        self._loader = config_loader
        self._agents: dict[str, AgentConfig] = {}
        logger.info("ConfigManager constructed")

    async def initialize(self) -> None:
        """Initialize by loading configuration."""
        logger.info("Loading initial configuration...")

        self._agents = self._loader.load_all_agents()
        self._initialized = True

        logger.info(f"Loaded {len(self._agents)} agents")

    async def shutdown(self) -> None:
        """Shutdown the configuration manager."""
        self._initialized = False
        logger.info("ConfigManager shutdown")

    # ========== Query Interface ==========

    @property
    def agents(self) -> dict[str, AgentConfig]:
        """Get current agent configurations.

        Returns:
            Dictionary mapping agent ID to AgentConfig
        """
        return self._agents.copy()

    def get_agent(self, agent_id: str) -> AgentConfig | None:
        """Get a specific agent by ID.

        Args:
            agent_id: The agent identifier

        Returns:
            AgentConfig or None if not found
        """
        return self._agents.get(agent_id)

    # ========== AgentInfoProvider Interface ==========

    def get_config(self, agent_id: str) -> AgentConfig | None:
        """Get agent configuration by ID (AgentInfoProvider interface).

        Args:
            agent_id: The unique agent identifier

        Returns:
            AgentConfig if found, None otherwise
        """
        return self._agents.get(agent_id)

    def get_all_ids(self) -> set[str]:
        """Get all agent IDs (AgentInfoProvider interface).

        Returns:
            Set of all registered agent IDs
        """
        return set(self._agents.keys())

    def is_proactive(self, agent_id: str) -> bool:
        """Check if an agent is proactive (AgentInfoProvider interface).

        Args:
            agent_id: The agent identifier

        Returns:
            True if agent responds to general messages
        """
        config = self._agents.get(agent_id)
        if config:
            return getattr(config, "proactive", False) or getattr(config, "respond_to_all", False)
        return False
