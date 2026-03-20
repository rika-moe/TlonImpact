"""Configuration loading for Tlon.

Provides unified configuration loading from YAML files.
"""

from pathlib import Path

import yaml

from tlon.base.settings import get_default_config_dir

from .models import AgentConfig, PositionConfig, PromptTemplates
from .validator import ConfigValidator, validate_yaml_structure


class ConfigLoadError(Exception):
    """Exception raised when configuration loading fails."""

    pass


class ConfigLoader:
    """Unified configuration loader.

    Loads Position, Agent, and Prompt configurations from YAML files.
    Provides caching and validation during loading.

    Attributes:
        config_dir: Root configuration directory
        positions_dir: Directory containing position YAML files
        agents_dir: Directory containing agent YAML files
    """

    def __init__(
        self,
        config_dir: str | None = None,
        positions_dir: str | None = None,
        agents_dir: str | None = None,
    ):
        """Initialize the configuration loader.

        Args:
            config_dir: Root configuration directory (default: project_root/config)
            positions_dir: Override for positions subdirectory
            agents_dir: Override for agents subdirectory
        """
        self.config_dir = Path(config_dir) if config_dir else get_default_config_dir()
        self.positions_dir = Path(positions_dir) if positions_dir else self.config_dir / "positions"
        self.agents_dir = Path(agents_dir) if agents_dir else self.config_dir / "agents"

        # Cache
        self._positions: dict[str, PositionConfig] | None = None
        self._agents: dict[str, AgentConfig] | None = None
        self._prompt_templates: PromptTemplates | None = None

    # =========================================================================
    # Position Loading
    # =========================================================================

    def load_all_positions(self, force_reload: bool = False) -> dict[str, PositionConfig]:
        """Load all positions from YAML files.

        Args:
            force_reload: Force reload from disk even if cached

        Returns:
            Dictionary mapping position type to PositionConfig
        """
        if self._positions is not None and not force_reload:
            return self._positions.copy()

        self._positions = {}

        if not self.positions_dir.exists():
            return self._positions.copy()

        yaml_files = list(self.positions_dir.glob("*.yaml"))

        for yaml_file in yaml_files:
            try:
                position = self._load_position_file(yaml_file)
                if position:
                    self._positions[position.type] = position
            except ConfigLoadError as e:
                # Log but continue loading other positions
                print(f"Warning: Failed to load position from {yaml_file}: {e}")

        return self._positions.copy()

    def load_position(self, position_type: str) -> PositionConfig | None:
        """Load a specific position by type.

        Args:
            position_type: Position type identifier

        Returns:
            PositionConfig or None if not found
        """
        # Try cache first
        if self._positions is not None:
            return self._positions.get(position_type)

        # Load from file
        filepath = self.positions_dir / f"{position_type}.yaml"
        if not filepath.exists():
            return None

        try:
            return self._load_position_file(filepath)
        except ConfigLoadError:
            return None

    def _load_position_file(self, filepath: Path) -> PositionConfig | None:
        """Load a single position from YAML file.

        Args:
            filepath: Path to YAML file

        Returns:
            PositionConfig or None if loading failed

        Raises:
            ConfigLoadError: If file is invalid
        """
        try:
            with open(filepath, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigLoadError(f"Invalid YAML syntax: {e}") from e
        except Exception as e:
            raise ConfigLoadError(f"Failed to read file: {e}") from e

        # Validate structure
        is_valid, errors = validate_yaml_structure(data, "position")
        if not is_valid:
            raise ConfigLoadError(f"Invalid structure: {', '.join(errors)}") from errors

        position_data = data["position"]

        # Ensure id is set (use type if not specified, or filename)
        if "id" not in position_data or not position_data["id"]:
            position_data["id"] = position_data.get("type", filepath.stem)

        # Ensure type is set (use id if not specified)
        if "type" not in position_data or not position_data["type"]:
            position_data["type"] = position_data.get("id", filepath.stem)

        # Validate data
        is_valid, errors = ConfigValidator.validate_position_data(position_data)
        if not is_valid:
            raise ConfigLoadError(f"Validation failed: {', '.join(errors)}") from errors

        return PositionConfig.from_dict(position_data)

    # =========================================================================
    # Agent Loading
    # =========================================================================

    def load_all_agents(self, force_reload: bool = False) -> dict[str, AgentConfig]:
        """Load all agents from YAML files.

        Args:
            force_reload: Force reload from disk even if cached

        Returns:
            Dictionary mapping agent ID to AgentConfig
        """
        if self._agents is not None and not force_reload:
            return self._agents.copy()

        self._agents = {}

        if not self.agents_dir.exists():
            return self._agents.copy()

        yaml_files = list(self.agents_dir.glob("*.yaml"))

        for yaml_file in yaml_files:
            try:
                agent = self._load_agent_file(yaml_file)
                if agent:
                    self._agents[agent.id] = agent
            except ConfigLoadError as e:
                # Log but continue loading other agents
                print(f"Warning: Failed to load agent from {yaml_file}: {e}")

        return self._agents.copy()

    def load_agent(self, agent_id: str) -> AgentConfig | None:
        """Load a specific agent by ID.

        Args:
            agent_id: Agent identifier

        Returns:
            AgentConfig or None if not found
        """
        # Try cache first
        if self._agents is not None:
            return self._agents.get(agent_id)

        # Load from file
        filepath = self.agents_dir / f"{agent_id}.yaml"
        if not filepath.exists():
            return None

        try:
            return self._load_agent_file(filepath)
        except ConfigLoadError:
            return None

    def load_agent_by_name(self, name: str) -> AgentConfig | None:
        """Load an agent by name.

        Args:
            name: Agent display name

        Returns:
            AgentConfig or None if not found
        """
        agents = self.load_all_agents()
        for agent in agents.values():
            if agent.name == name:
                return agent
        return None

    def _load_agent_file(self, filepath: Path) -> AgentConfig | None:
        """Load a single agent from YAML file.

        Args:
            filepath: Path to YAML file

        Returns:
            AgentConfig or None if loading failed

        Raises:
            ConfigLoadError: If file is invalid
        """
        try:
            with open(filepath, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigLoadError(f"Invalid YAML syntax: {e}") from e
        except Exception as e:
            raise ConfigLoadError(f"Failed to read file: {e}") from e

        # Validate structure
        is_valid, errors = validate_yaml_structure(data, "agent")
        if not is_valid:
            raise ConfigLoadError(f"Invalid structure: {', '.join(errors)}") from errors

        agent_data = data["agent"]

        # Backward compat: if agent has position_id but no role, try to load role from position file
        if "position_id" in agent_data and agent_data["position_id"] and "role" not in agent_data:
            position_id = agent_data["position_id"]
            position_file = self.positions_dir / f"{position_id}.yaml"
            if position_file.exists():
                try:
                    with open(position_file, encoding="utf-8") as pf:
                        pos_data = yaml.safe_load(pf)
                    if isinstance(pos_data, dict) and "position" in pos_data:
                        pos_fields = pos_data["position"]
                        # Build inline role from position data
                        agent_data["role"] = {
                            "title": pos_fields.get("title", ""),
                            "jd": pos_fields.get("jd", pos_fields.get("description", "")),
                            "department": pos_fields.get("department", ""),
                            "rules": pos_fields.get("rules", []),
                            "contact_when": pos_fields.get("contact_when", ""),
                            "prompt_overrides": pos_fields.get("prompt_overrides"),
                        }
                except Exception:
                    pass  # If migration fails, continue without role

        # Validate data
        is_valid, errors = ConfigValidator.validate_agent_data(agent_data)
        if not is_valid:
            raise ConfigLoadError(f"Validation failed: {', '.join(errors)}") from errors

        return AgentConfig.from_dict(agent_data)

    # =========================================================================
    # Prompt Templates Loading
    # =========================================================================

    def load_prompt_templates(
        self,
        filepath: str | None = None,
        force_reload: bool = False,
    ) -> PromptTemplates:
        """Load global prompt templates from YAML file.

        Args:
            filepath: Path to templates YAML file (default: config/prompt_templates.yaml)
            force_reload: Force reload from disk even if cached

        Returns:
            PromptTemplates instance (uses defaults if file not found)
        """
        if self._prompt_templates is not None and not force_reload:
            return self._prompt_templates

        filepath = self.config_dir / "prompt_templates.yaml" if filepath is None else Path(filepath)

        if not filepath.exists():
            # Return defaults
            self._prompt_templates = PromptTemplates()
            return self._prompt_templates

        try:
            with open(filepath, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"Warning: Invalid YAML in {filepath}: {e}. Using defaults.")
            self._prompt_templates = PromptTemplates()
            return self._prompt_templates
        except Exception as e:
            print(f"Warning: Failed to read {filepath}: {e}. Using defaults.")
            self._prompt_templates = PromptTemplates()
            return self._prompt_templates

        # Validate structure
        if not isinstance(data, dict) or "templates" not in data:
            print(f"Warning: Invalid structure in {filepath}. Using defaults.")
            self._prompt_templates = PromptTemplates()
            return self._prompt_templates

        templates_data = data["templates"]

        # Validate data
        is_valid, errors = ConfigValidator.validate_prompt_templates(templates_data)
        if not is_valid:
            print(f"Warning: Invalid templates data: {', '.join(errors)}. Using defaults.")
            self._prompt_templates = PromptTemplates()
            return self._prompt_templates

        self._prompt_templates = PromptTemplates.from_dict(templates_data)
        return self._prompt_templates

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def clear_cache(self) -> None:
        """Clear all cached configurations."""
        self._positions = None
        self._agents = None
        self._prompt_templates = None

    def get_agents_by_position(self, position_id: str) -> dict[str, AgentConfig]:
        """Get all agents with a specific position.

        Args:
            position_id: Position type to filter by

        Returns:
            Dictionary of agent ID to AgentConfig
        """
        agents = self.load_all_agents()
        return {
            agent_id: agent
            for agent_id, agent in agents.items()
            if agent.position_id == position_id
        }


__all__ = [
    "ConfigLoader",
    "ConfigLoadError",
]
