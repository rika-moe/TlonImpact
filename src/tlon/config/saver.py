"""Configuration saving for Tlon.

Provides unified configuration saving to YAML files.
"""

import shutil
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

from ..settings import get_default_config_dir, get_default_data_dir
from .models import AgentConfig, PositionConfig, PromptTemplates
from .validator import ConfigValidator


class ConfigSaveError(Exception):
    """Exception raised when configuration saving fails."""
    pass


class ConfigSaver:
    """Unified configuration saver.
    
    Saves Position, Agent, and Prompt configurations to YAML files.
    Provides validation and backup functionality.
    
    Attributes:
        config_dir: Root configuration directory
        positions_dir: Directory for position YAML files
        agents_dir: Directory for agent YAML files
        data_dir: Base directory for agent data
    """
    
    def __init__(
        self,
        config_dir: Optional[str] = None,
        positions_dir: Optional[str] = None,
        agents_dir: Optional[str] = None,
        data_dir: Optional[str] = None,
    ):
        """Initialize the configuration saver.
        
        Args:
            config_dir: Root configuration directory (default: project_root/config)
            positions_dir: Override for positions subdirectory
            agents_dir: Override for agents subdirectory
            data_dir: Base directory for agent data storage (default: project_root/data)
        """
        self.config_dir = Path(config_dir) if config_dir else get_default_config_dir()
        self.positions_dir = Path(positions_dir) if positions_dir else self.config_dir / "positions"
        self.agents_dir = Path(agents_dir) if agents_dir else self.config_dir / "agents"
        self.data_dir = Path(data_dir) if data_dir else get_default_data_dir()
    
    def _ensure_directory(self, directory: Path) -> None:
        """Ensure a directory exists."""
        directory.mkdir(parents=True, exist_ok=True)
    
    # =========================================================================
    # Position Saving
    # =========================================================================
    
    def save_position(
        self,
        position: PositionConfig,
        create_backup: bool = False,
    ) -> Tuple[bool, str]:
        """Save a position to YAML file.
        
        Args:
            position: PositionConfig to save
            create_backup: Whether to create a backup of existing file
            
        Returns:
            Tuple of (success, message)
        """
        # Validate first
        is_valid, errors = ConfigValidator.validate_position_config(position)
        if not is_valid:
            return False, f"Validation failed: {', '.join(errors)}"
        
        try:
            self._ensure_directory(self.positions_dir)
            
            # Prepare data
            data = {"position": position.to_dict()}
            
            # Write to file
            filename = f"{position.type}.yaml"
            filepath = self.positions_dir / filename
            
            # Create backup if requested and file exists
            if create_backup and filepath.exists():
                backup_path = filepath.with_suffix(".yaml.bak")
                shutil.copy2(filepath, backup_path)
            
            with open(filepath, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, sort_keys=False)
            
            return True, f"Position saved to {filepath}"
            
        except Exception as e:
            return False, f"Failed to save position: {e}"
    
    def delete_position(
        self,
        position_type: str,
        agent_ids: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        """Delete a position.
        
        Args:
            position_type: Position type to delete
            agent_ids: Optional list of agent IDs using this position
            
        Returns:
            Tuple of (success, message)
        """
        # Check if any agents use this position
        if agent_ids:
            return False, f"Cannot delete: {len(agent_ids)} agent(s) are using this position"
        
        try:
            filename = f"{position_type}.yaml"
            filepath = self.positions_dir / filename
            
            if not filepath.exists():
                return False, f"Position '{position_type}' not found"
            
            filepath.unlink()
            return True, f"Position '{position_type}' deleted"
            
        except Exception as e:
            return False, f"Failed to delete position: {e}"
    
    # =========================================================================
    # Agent Saving
    # =========================================================================
    
    def save_agent(
        self,
        agent: AgentConfig,
        create_backup: bool = False,
        create_data_dir: bool = True,
    ) -> Tuple[bool, str]:
        """Save an agent to YAML file.
        
        Args:
            agent: AgentConfig to save
            create_backup: Whether to create a backup of existing file
            create_data_dir: Whether to create agent data directory
            
        Returns:
            Tuple of (success, message)
        """
        # Validate first
        is_valid, errors = ConfigValidator.validate_agent_config(agent)
        if not is_valid:
            return False, f"Validation failed: {', '.join(errors)}"
        
        try:
            self._ensure_directory(self.agents_dir)
            
            # Prepare data
            data = {"agent": agent.to_dict()}
            
            # Write to file
            filename = f"{agent.id}.yaml"
            filepath = self.agents_dir / filename
            
            # Create backup if requested and file exists
            if create_backup and filepath.exists():
                backup_path = filepath.with_suffix(".yaml.bak")
                shutil.copy2(filepath, backup_path)
            
            with open(filepath, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, sort_keys=False)
            
            # Create data directory if requested
            if create_data_dir:
                agent.get_data_dir(self.data_dir)
            
            return True, f"Agent saved to {filepath}"
            
        except Exception as e:
            return False, f"Failed to save agent: {e}"
    
    def delete_agent(
        self,
        agent_id: str,
        delete_data: bool = True,
    ) -> Tuple[bool, str]:
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
            
            # Delete data directory if requested
            if delete_data:
                agent_data_dir = self.data_dir / "agents" / agent_id
                if agent_data_dir.exists():
                    shutil.rmtree(agent_data_dir)
            
            return True, f"Agent '{agent_id}' deleted"
            
        except Exception as e:
            return False, f"Failed to delete agent: {e}"
    
    def transfer_agent_position(
        self,
        agent: AgentConfig,
        new_position_id: str,
    ) -> Tuple[bool, str]:
        """Transfer an agent to a new position.
        
        Args:
            agent: AgentConfig to update
            new_position_id: New position type
            
        Returns:
            Tuple of (success, message)
        """
        old_position_id = agent.position_id
        agent.position_id = new_position_id
        
        success, msg = self.save_agent(agent)
        if success:
            return True, f"Agent transferred from '{old_position_id}' to '{new_position_id}'"
        else:
            # Revert on failure
            agent.position_id = old_position_id
            return False, msg
    
    # =========================================================================
    # Prompt Templates Saving
    # =========================================================================
    
    def save_prompt_templates(
        self,
        templates: PromptTemplates,
        filepath: Optional[str] = None,
        create_backup: bool = False,
    ) -> Tuple[bool, str]:
        """Save global prompt templates to YAML file.
        
        Args:
            templates: PromptTemplates to save
            filepath: Path to save to (default: config/prompt_templates.yaml)
            create_backup: Whether to create a backup of existing file
            
        Returns:
            Tuple of (success, message)
        """
        # Validate first
        is_valid, errors = ConfigValidator.validate_prompt_templates_config(templates)
        if not is_valid:
            return False, f"Validation failed: {', '.join(errors)}"
        
        try:
            if filepath is None:
                filepath = self.config_dir / "prompt_templates.yaml"
            else:
                filepath = Path(filepath)
            
            self._ensure_directory(filepath.parent)
            
            # Prepare data
            data = {"templates": templates.to_dict()}
            
            # Create backup if requested and file exists
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
        position_type: str,
        existing_ids: Optional[List[str]] = None,
    ) -> str:
        """Generate a unique agent ID.
        
        Args:
            position_type: Position type (used as prefix)
            existing_ids: List of existing IDs to avoid
            
        Returns:
            Unique agent ID
        """
        if existing_ids is None:
            existing_ids = []
        
        # Normalize position type for ID
        prefix = position_type.replace("_", "-")
        
        # Find next available number
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
