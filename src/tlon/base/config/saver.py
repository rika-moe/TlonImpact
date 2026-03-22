"""Configuration saving for Tlon.

Provides unified configuration saving to YAML files.
"""

import shutil
from pathlib import Path

import yaml

from tlon.base.settings import get_default_config_dir, get_default_data_dir

from .models import AgentConfig, PromptTemplates
from .validator import ConfigValidator


class ConfigSaveError(Exception):
    """Exception raised when configuration saving fails."""

    pass


class ConfigSaver:
    """Unified configuration saver.

    Saves Agent and Prompt configurations to YAML files.
    Provides validation and backup functionality.

    Attributes:
        config_dir: Root configuration directory
        agents_dir: Directory for agent YAML files
        data_dir: Base directory for agent data
    """

    def __init__(
        self,
        config_dir: str | None = None,
        agents_dir: str | None = None,
        data_dir: str | None = None,
        **kwargs,
    ):
        """Initialize the configuration saver.

        Args:
            config_dir: Root configuration directory (default: project_root/config)
            agents_dir: Override for agents subdirectory
            data_dir: Base directory for agent data storage (default: project_root/data)
        """
        self.config_dir = Path(config_dir) if config_dir else get_default_config_dir()
        self.agents_dir = Path(agents_dir) if agents_dir else self.config_dir / "agents"
        self.data_dir = Path(data_dir) if data_dir else get_default_data_dir()

    def _ensure_directory(self, directory: Path) -> None:
        """Ensure a directory exists."""
        directory.mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # Agent Saving
    # =========================================================================

    def save_agent(
        self,
        agent: AgentConfig,
        create_backup: bool = False,
        create_data_dir: bool = True,
    ) -> tuple[bool, str]:
        """Save an agent to YAML file.

        Args:
            agent: AgentConfig to save
            create_backup: Whether to create a backup of existing file
            create_data_dir: Whether to create agent data directory

        Returns:
            Tuple of (success, message)
        """
        is_valid, errors = ConfigValidator.validate_agent_config(agent)
        if not is_valid:
            return False, f"Validation failed: {', '.join(errors)}"

        try:
            self._ensure_directory(self.agents_dir)

            data = {"agent": agent.to_dict()}

            filename = f"{agent.id}.yaml"
            filepath = self.agents_dir / filename

            if create_backup and filepath.exists():
                backup_path = filepath.with_suffix(".yaml.bak")
                shutil.copy2(filepath, backup_path)

            with open(filepath, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, sort_keys=False)

            if create_data_dir:
                agent.get_data_dir(self.data_dir)

            return True, f"Agent saved to {filepath}"

        except Exception as e:
            return False, f"Failed to save agent: {e}"

    def delete_agent(
        self,
        agent_id: str,
        delete_data: bool = True,
    ) -> tuple[bool, str]:
        """Delete an agent.

        Args:
            agent_id: Agent ID to delete
            delete_data: Also delete agent data directory

        Returns:
            Tuple of (success, message)
        """
        try:
            filename = f"{agent_id}.yaml"
            filepath = self.agents_dir / filename

            if not filepath.exists():
                return False, f"Agent '{agent_id}' not found"

            filepath.unlink()

            if delete_data:
                agent_data_dir = self.data_dir / "agents" / agent_id
                if agent_data_dir.exists():
                    shutil.rmtree(agent_data_dir)

            return True, f"Agent '{agent_id}' deleted"

        except Exception as e:
            return False, f"Failed to delete agent: {e}"

    # =========================================================================
    # Prompt Templates Saving
    # =========================================================================

    def save_prompt_templates(
        self,
        templates: PromptTemplates,
        filepath: str | None = None,
        create_backup: bool = False,
    ) -> tuple[bool, str]:
        """Save global prompt templates to YAML file.

        Args:
            templates: PromptTemplates to save
            filepath: Path to save to (default: config/prompt_templates.yaml)
            create_backup: Whether to create a backup of existing file

        Returns:
            Tuple of (success, message)
        """
        is_valid, errors = ConfigValidator.validate_prompt_templates_config(templates)
        if not is_valid:
            return False, f"Validation failed: {', '.join(errors)}"

        try:
            if filepath is None:
                filepath = self.config_dir / "prompt_templates.yaml"
            else:
                filepath = Path(filepath)

            self._ensure_directory(filepath.parent)

            data = {"templates": templates.to_dict()}

            if create_backup and filepath.exists():
                backup_path = filepath.with_suffix(".yaml.bak")
                shutil.copy2(filepath, backup_path)

            with open(filepath, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, sort_keys=False)

            return True, f"Prompt templates saved to {filepath}"

        except Exception as e:
            return False, f"Failed to save prompt templates: {e}"

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def generate_agent_id(
        self,
        prefix: str,
        existing_ids: list[str] | None = None,
    ) -> str:
        """Generate a unique agent ID.

        Args:
            prefix: Prefix for the agent ID
            existing_ids: List of existing IDs to avoid

        Returns:
            Unique agent ID
        """
        if existing_ids is None:
            existing_ids = []

        prefix = prefix.replace("_", "-")

        counter = 1
        while True:
            agent_id = f"{prefix}-{counter:03d}"
            if agent_id not in existing_ids:
                return agent_id
            counter += 1


__all__ = [
    "ConfigSaver",
    "ConfigSaveError",
]
