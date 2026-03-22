"""Configuration loading for Tlon.

Provides unified configuration loading from YAML files.
"""

from pathlib import Path

import yaml

from tlon.base.settings import get_default_config_dir

from .models import AgentConfig, PromptTemplates
from .validator import ConfigValidator, validate_yaml_structure


class ConfigLoadError(Exception):
    """Exception raised when configuration loading fails."""

    pass


class ConfigLoader:
    """Unified configuration loader.

    Loads Agent and Prompt configurations from YAML files.
    Provides caching and validation during loading.

    Attributes:
        config_dir: Root configuration directory
        agents_dir: Directory containing agent YAML files
    """

    def __init__(
        self,
        config_dir: str | None = None,
        agents_dir: str | None = None,
        **kwargs,
    ):
        """Initialize the configuration loader.

        Args:
            config_dir: Root configuration directory (default: project_root/config)
            agents_dir: Override for agents subdirectory
        """
        self.config_dir = Path(config_dir) if config_dir else get_default_config_dir()
        self.agents_dir = Path(agents_dir) if agents_dir else self.config_dir / "agents"

        # Cache
        self._agents: dict[str, AgentConfig] | None = None
        self._prompt_templates: PromptTemplates | None = None

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
                print(f"Warning: Failed to load agent from {yaml_file}: {e}")

        return self._agents.copy()

    def load_agent(self, agent_id: str) -> AgentConfig | None:
        """Load a specific agent by ID.

        Args:
            agent_id: Agent identifier

        Returns:
            AgentConfig or None if not found
        """
        if self._agents is not None:
            return self._agents.get(agent_id)

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

        is_valid, errors = validate_yaml_structure(data, "agent")
        if not is_valid:
            raise ConfigLoadError(f"Invalid structure: {', '.join(errors)}") from errors

        agent_data = data["agent"]

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

        if not isinstance(data, dict) or "templates" not in data:
            print(f"Warning: Invalid structure in {filepath}. Using defaults.")
            self._prompt_templates = PromptTemplates()
            return self._prompt_templates

        templates_data = data["templates"]

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
        self._agents = None
        self._prompt_templates = None


__all__ = [
    "ConfigLoader",
    "ConfigLoadError",
]
