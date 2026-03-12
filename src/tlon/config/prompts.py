"""Prompt template management for Tlon.

Provides centralized prompt template management with support for:
- Global default templates
- Position-level overrides (complete replacement)
- Template composition for system prompts
"""

from typing import TYPE_CHECKING, List, Optional

from .loader import ConfigLoader
from .models import PositionConfig, PromptTemplates

if TYPE_CHECKING:
    from .models import AgentConfig


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
    
    def build_team_context_prompt(
        self,
        team_members: List[tuple],  # List of (name, position_title, description, contact_when)
    ) -> str:
        """Build the team context prompt section.
        
        Args:
            team_members: List of tuples (name, position_title, description, contact_when)
            
        Returns:
            Team context prompt string
        """
        if not team_members:
            return ""
        
        lines = ["## 你的团队", ""]
        for name, title, desc, when in team_members:
            lines.append(f"- {name}（{title}）：{desc}")
            lines.append(f"  - 何时联系：{when}")
            lines.append("")
        
        return "\n".join(lines)
    
    def build_role_prompt(self, position: Optional[PositionConfig] = None) -> str:
        """Build the role description prompt section.
        
        Args:
            position: PositionConfig with job description
            
        Returns:
            Role prompt string
        """
        if position and position.jd:
            return f"""## 岗位职责

{position.jd}"""
        return ""
    
    def build_system_prompt(
        self,
        agent: "AgentConfig",
        position: Optional[PositionConfig] = None,
        team_members: Optional[List[tuple]] = None,
    ) -> str:
        """Build the complete system prompt for an agent.
        
        Combines all prompt sections into a complete system prompt.
        
        Args:
            agent: AgentConfig instance
            position: PositionConfig instance (loaded from agent.position_id if not provided)
            team_members: Optional list of team member info tuples
            
        Returns:
            Complete system prompt string
        """
        # Load position if not provided
        if position is None:
            position = self.loader.load_position(agent.position_id)
        
        # Get templates
        templates = self.get_templates_for_position(position)
        
        # Build sections
        sections = []
        
        # Identity section
        position_title = position.title if position else agent.position_id
        sections.append(f"你是{agent.name}，一名{position_title}。")
        sections.append("")
        
        # Role description
        if position and position.jd:
            sections.append(self.build_role_prompt(position))
            sections.append("")
        
        # Personality
        if agent.personality:
            sections.append(f"你性格特点: {agent.personality}")
            sections.append("")
        
        # Team context
        if team_members:
            sections.append(self.build_team_context_prompt(team_members))
            sections.append("")
        
        # Workflow
        sections.append(f"## 工作流程\n\n{templates.workflow}")
        sections.append("")
        
        # Important notes
        sections.append(f"## 重要提示\n\n{templates.important_notes}")
        sections.append("")
        
        # Discipline rules
        position_rules = position.rules if position else []
        discipline = self.build_discipline_rules_prompt(
            position=position,
            position_rules=position_rules,
        )
        sections.append(discipline)
        
        return "\n".join(sections)
    
    def reload_templates(self) -> None:
        """Reload all templates from disk."""
        self._global_templates = None
        self.loader.clear_cache()


__all__ = [
    "PromptTemplateManager",
]
