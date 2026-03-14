"""Prompt template management for Tlon.

Provides centralized prompt template management with support for:
- Global default templates
- Position-level overrides (complete replacement)
- Template composition for system prompts
"""

from typing import TYPE_CHECKING, List, Optional

from .loader import ConfigLoader
from .models import PositionConfig, PromptTemplates
from ..logger import get_logger

if TYPE_CHECKING:
    from .models import AgentConfig

logger = get_logger(__name__)

class PromptTemplateManager:
    """Manages prompt templates with three-layer architecture.
    
    Architecture:
        1. Global Templates (config/prompt_templates.yaml) - Default values
        2. Position Overrides (config/positions/*.yaml) - Complete replacement
        3. Runtime - Agent uses merged templates
    
    Override Strategy:
        - Position's `prompt_overrides` completely replaces global templates
        - Not append, but full replacement
        - Each of workflow, important_notes, discipline_rules can be overridden independently
    
    Example:
        # Global default
        workflow: "Default workflow..."
        
        # Position override (complete replacement)
        prompt_overrides:
          workflow: "Custom workflow for this position..."
    
    Attributes:
        loader: ConfigLoader instance for loading configurations
        _global_templates: Cached global templates
    """
    
    def __init__(self, loader: Optional[ConfigLoader] = None):
        """Initialize the prompt template manager.
        
        Args:
            loader: ConfigLoader instance (creates default if None)
        """
        self.loader = loader or ConfigLoader()
        self._global_templates: Optional[PromptTemplates] = None
    
    def load_global_templates(self, force_reload: bool = False) -> PromptTemplates:
        """Load global prompt templates.
        
        Args:
            force_reload: Force reload from disk
            
        Returns:
            Global PromptTemplates
        """
        if self._global_templates is None or force_reload:
            self._global_templates = self.loader.load_prompt_templates(force_reload=force_reload)
        return self._global_templates
    
    def get_templates_for_position(
        self,
        position: Optional[PositionConfig] = None,
        position_type: Optional[str] = None,
    ) -> PromptTemplates:
        """Get merged templates for a position.
        
        Applies position-level overrides to global templates.
        Overrides are complete replacements (not append).
        
        Args:
            position: PositionConfig with optional overrides
            position_type: Position type to load if position not provided
            
        Returns:
            Merged PromptTemplates
        """
        global_templates = self.load_global_templates()
        
        # Load position if only type provided
        if position is None and position_type:
            position = self.loader.load_position(position_type)
        
        # Apply overrides if position has them
        if position and position.prompt_overrides:
            return global_templates.merge_with_overrides(position.prompt_overrides)
        
        return global_templates
    
    def get_templates_for_agent(self, agent: "AgentConfig") -> PromptTemplates:
        """Get merged templates for an agent's position.
        
        Args:
            agent: AgentConfig instance
            
        Returns:
            Merged PromptTemplates
        """
        return self.get_templates_for_position(position_type=agent.position_id)
    
    def build_workflow_prompt(
        self,
        position: Optional[PositionConfig] = None,
        position_type: Optional[str] = None,
    ) -> str:
        """Build the workflow prompt section.
        
        Args:
            position: PositionConfig with optional overrides
            position_type: Position type to load if position not provided
            
        Returns:
            Workflow prompt string
        """
        templates = self.get_templates_for_position(position, position_type)
        return f"""## 工作流程

{templates.workflow}"""
    
    def build_important_notes_prompt(
        self,
        position: Optional[PositionConfig] = None,
        position_type: Optional[str] = None,
    ) -> str:
        """Build the important notes prompt section.
        
        Args:
            position: PositionConfig with optional overrides
            position_type: Position type to load if position not provided
            
        Returns:
            Important notes prompt string
        """
        templates = self.get_templates_for_position(position, position_type)
        return f"""## 重要提示

{templates.important_notes}"""
    
    def build_discipline_rules_prompt(
        self,
        position: Optional[PositionConfig] = None,
        position_type: Optional[str] = None,
        position_rules: Optional[List[str]] = None,
    ) -> str:
        """Build the discipline rules prompt section.
        
        Combines global/position discipline rules with position-specific rules.
        
        Args:
            position: PositionConfig with optional overrides
            position_type: Position type to load if position not provided
            position_rules: Additional position-specific rules
            
        Returns:
            Discipline rules prompt string
        """
        templates = self.get_templates_for_position(position, position_type)
        
        rules_text = templates.discipline_rules
        
        # Add position-specific rules if provided
        if position_rules:
            additional_rules = "\n".join(f"{i+5}. {rule}" for i, rule in enumerate(position_rules))
            rules_text = f"{rules_text}\n{additional_rules}"
        
        return f"""## 工作纪律

{rules_text}"""
    
    def reload_templates(self) -> None:
        """Reload all templates from disk."""
        self._global_templates = None
        self.loader.clear_cache()


__all__ = [
    "PromptTemplateManager",
]
