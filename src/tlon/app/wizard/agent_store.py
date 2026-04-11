"""Agent data storage operations.

Handles loading, saving, and managing Agent configurations
from individual YAML files in config/agents/ directory.
"""

from pathlib import Path

import yaml
from pydantic import ValidationError

from tlon.base.config import AgentConfig
from tlon.base.logger import get_logger
from tlon.base.settings import get_default_config_dir, get_default_data_dir

logger = get_logger(__name__)


class AgentStore:
    """Store for managing Agent configurations.

    Provides CRUD operations for agent YAML files.

    Attributes:
        agents_dir: Directory containing agent YAML files
        data_dir: Base directory for agent data storage
    """

    def __init__(self, agents_dir: str | None = None, data_dir: str | None = None):
        """Initialize the agent store.

        Args:
            agents_dir: Directory containing agent YAML files.
                Defaults to $TLON_HOME/config/agents.
            data_dir: Base directory for agent data storage.
                Defaults to $TLON_HOME/data.
        """
        cfg = get_default_config_dir()
        resolved_agents_dir = agents_dir if agents_dir is not None else str(cfg / "agents")
        resolved_data_dir = data_dir if data_dir is not None else str(get_default_data_dir())

        self.agents_dir = Path(resolved_agents_dir)
        self.data_dir = Path(resolved_data_dir)
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        """Ensure the agents directory exists."""
        self.agents_dir.mkdir(parents=True, exist_ok=True)

    def _load_agent_file(self, filepath: Path) -> AgentConfig | None:
        """Load a single agent from YAML file."""
        try:
            data = yaml.safe_load(filepath.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or "agent" not in data:
                return None
            return AgentConfig.model_validate(data["agent"])
        except (yaml.YAMLError, ValidationError, Exception) as e:
            logger.warning(f"Failed to load agent from {filepath}: {e}")
            return None

    def load_all_agents(self) -> dict[str, AgentConfig]:
        """Load all agents from YAML files.

        Returns:
            Dictionary mapping agent ID to AgentConfig object
        """
        agents: dict[str, AgentConfig] = {}
        if not self.agents_dir.exists():
            return agents

        for yaml_file in self.agents_dir.glob("*.yaml"):
            agent = self._load_agent_file(yaml_file)
            if agent:
                agents[agent.id] = agent
        return agents

    def get_agent(self, agent_id: str) -> AgentConfig | None:
        """Get an agent by ID.

        Args:
            agent_id: Agent identifier

        Returns:
            AgentConfig object or None if not found
        """
        filepath = self.agents_dir / f"{agent_id}.yaml"
        if not filepath.exists():
            return None
        return self._load_agent_file(filepath)

    def get_agent_by_name(self, name: str) -> AgentConfig | None:
        """Get an agent by name.

        Args:
            name: Agent display name

        Returns:
            AgentConfig object or None if not found
        """
        for agent in self.load_all_agents().values():
            if agent.name == name:
                return agent
        return None

    def save_agent(self, agent: AgentConfig) -> tuple[bool, str]:
        """Save an agent to YAML file.

        Args:
            agent: AgentConfig to save

        Returns:
            Tuple of (success, message)
        """
        try:
            self._ensure_directory()
            data = {"agent": agent.model_dump(exclude_none=True)}
            filepath = self.agents_dir / f"{agent.id}.yaml"
            filepath.write_text(
                yaml.dump(data, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            # Create data directory
            agent.get_data_dir(self.data_dir)
            return True, f"Agent saved to {filepath}"
        except Exception as e:
            return False, f"Failed to save agent: {e}"

    def delete_agent(self, agent_id: str, delete_data: bool = True) -> tuple[bool, str]:
        """Delete an agent.

        Args:
            agent_id: Agent ID to delete
            delete_data: Also delete agent data directory

        Returns:
            Tuple of (success, message)
        """
        try:
            filepath = self.agents_dir / f"{agent_id}.yaml"
            if not filepath.exists():
                return False, f"Agent '{agent_id}' not found"

            filepath.unlink()

            if delete_data:
                import shutil

                agent_data_dir = self.data_dir / "agents" / agent_id
                if agent_data_dir.exists():
                    shutil.rmtree(agent_data_dir)

            return True, f"Agent '{agent_id}' deleted"
        except Exception as e:
            return False, f"Failed to delete agent: {e}"

    def agent_exists(self, agent_id: str) -> bool:
        """Check if an agent exists.

        Args:
            agent_id: Agent ID to check

        Returns:
            True if agent exists
        """
        return self.get_agent(agent_id) is not None

    def generate_agent_id(self, prefix: str, existing_ids: list[str] | None = None) -> str:
        """Generate a unique agent ID.

        Args:
            prefix: Prefix for the agent ID
            existing_ids: List of existing IDs to avoid

        Returns:
            Unique agent ID
        """
        if existing_ids is None:
            existing_ids = list(self.load_all_agents().keys())

        prefix = prefix.replace("_", "-")
        counter = 1
        while True:
            agent_id = f"{prefix}-{counter:03d}"
            if agent_id not in existing_ids:
                return agent_id
            counter += 1

    def validate_agent_data(self, data: dict) -> tuple[bool, list[str]]:
        """Validate agent data.

        Args:
            data: Agent data dictionary

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        try:
            AgentConfig.model_validate(data)
            return True, []
        except ValidationError as e:
            errors = [f"{err['loc']}: {err['msg']}" for err in e.errors()]
            return False, errors


__all__ = ["AgentStore"]
