"""Agent data storage operations.

Handles loading, saving, and managing Agent configurations
from individual YAML files in config/agents/ directory.

This module is a thin wrapper around ConfigLoader/ConfigSaver
with additional convenience methods for agent management.
"""

from pathlib import Path

from tlon.base.config.loader import ConfigLoader
from tlon.base.config.models import AgentConfig
from tlon.base.config.saver import ConfigSaver
from tlon.base.config.validator import ConfigValidator
from tlon.base.logger import get_logger

logger = get_logger(__name__)


class AgentStore:
    """Store for managing Agent configurations.

    A thin wrapper around ConfigLoader/ConfigSaver that provides
    CRUD operations with reference management.

    Attributes:
        agents_dir: Directory containing agent YAML files
        data_dir: Base directory for agent data storage
        _loader: ConfigLoader instance for loading
        _saver: ConfigSaver instance for saving
    """

    def __init__(self, agents_dir: str = "config/agents", data_dir: str = "data"):
        """Initialize the agent store.

        Args:
            agents_dir: Directory containing agent YAML files
            data_dir: Base directory for agent data storage
        """
        self.agents_dir = Path(agents_dir)
        self.data_dir = Path(data_dir)
        self._loader = ConfigLoader(agents_dir=agents_dir)
        self._saver = ConfigSaver(agents_dir=agents_dir, data_dir=data_dir)
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        """Ensure the agents directory exists."""
        self.agents_dir.mkdir(parents=True, exist_ok=True)

    def load_all_agents(self, force_reload: bool = False) -> dict[str, AgentConfig]:
        """Load all agents from YAML files.

        Args:
            force_reload: Force reload from disk even if cached

        Returns:
            Dictionary mapping agent ID to AgentConfig object
        """
        return self._loader.load_all_agents(force_reload=force_reload)

    def get_agent(self, agent_id: str) -> AgentConfig | None:
        """Get an agent by ID.

        Args:
            agent_id: Agent identifier

        Returns:
            AgentConfig object or None if not found
        """
        return self._loader.load_agent(agent_id)

    def get_agent_by_name(self, name: str) -> AgentConfig | None:
        """Get an agent by name.

        Args:
            name: Agent display name

        Returns:
            AgentConfig object or None if not found
        """
        return self._loader.load_agent_by_name(name)

    def save_agent(self, agent: AgentConfig) -> tuple[bool, str]:
        """Save an agent to YAML file.

        Uses ConfigSaver for the actual save operation.

        Args:
            agent: AgentConfig to save

        Returns:
            Tuple of (success, message)
        """
        return self._saver.save_agent(agent, create_data_dir=True)

    def delete_agent(self, agent_id: str, delete_data: bool = True) -> tuple[bool, str]:
        """Delete an agent.

        Args:
            agent_id: Agent ID to delete
            delete_data: Also delete agent data directory

        Returns:
            Tuple of (success, message)
        """
        return self._saver.delete_agent(agent_id, delete_data=delete_data)

    def get_agents_by_position(self, position_id: str) -> list[AgentConfig]:
        """Get all agents with a specific position.

        Args:
            position_id: Position type to filter by

        Returns:
            List of AgentConfig objects
        """
        return list(self._loader.get_agents_by_position(position_id).values())

    def agent_exists(self, agent_id: str) -> bool:
        """Check if an agent exists.

        Args:
            agent_id: Agent ID to check

        Returns:
            True if agent exists
        """
        return self.get_agent(agent_id) is not None

    def generate_agent_id(self, position_type: str, existing_ids: list[str] | None = None) -> str:
        """Generate a unique agent ID.

        Args:
            position_type: Position type (used as prefix)
            existing_ids: List of existing IDs to avoid

        Returns:
            Unique agent ID
        """
        if existing_ids is None:
            existing_ids = list(self.load_all_agents().keys())

        return self._saver.generate_agent_id(position_type, existing_ids)

    def transfer_position(self, agent_id: str, new_position_id: str) -> tuple[bool, str]:
        """Transfer an agent to a new position.

        Args:
            agent_id: Agent ID to transfer
            new_position_id: New position type

        Returns:
            Tuple of (success, message)
        """
        agent = self.get_agent(agent_id)
        if not agent:
            return False, f"Agent '{agent_id}' not found"

        return self._saver.transfer_agent_position(agent, new_position_id)

    def validate_agent_data(self, data: dict) -> tuple[bool, list[str]]:
        """Validate agent data.

        Args:
            data: Agent data dictionary

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        return ConfigValidator.validate_agent_data(data)


__all__ = ["AgentStore"]
