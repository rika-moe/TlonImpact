"""Prompt template rendering for Positions.

This module provides prompt template rendering using Jinja2,
allowing positions to define dynamic system prompts.
"""

from typing import Any, Dict, Optional

from jinja2 import Template, Environment, BaseLoader

from .models import Position


class PromptRenderer:
    """Renderer for position prompt templates.
    
    Uses Jinja2 to render position prompt templates with agent-specific data.
    
    Example:
        >>> position = Position(
        ...     id="developer",
        ...     prompt_template="# {{ agent.name }}\\n岗位: {{ position.title }}"
        ... )
        >>> renderer = PromptRenderer()
        >>> prompt = renderer.render(
        ...     position=position,
        ...     agent={"name": "林知遥", "personality": "细心"}
        ... )
        >>> print(prompt)
        # 林知遥
        岗位: 开发工程师
    """
    
    def __init__(self):
        """Initialize the prompt renderer."""
        # Create a Jinja2 environment without file system loader
        # (we only render strings)
        self.env = Environment(loader=BaseLoader())
    
    def render(
        self,
        position: Position,
        agent: Dict[str, Any],
        extra_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Render a position's prompt template.
        
        Args:
            position: Position with prompt_template
            agent: Agent data (name, personality, etc.)
            extra_context: Additional context for template rendering
            
        Returns:
            Rendered prompt string
            
        Raises:
            ValueError: If position has no prompt_template
        """
        if not position.prompt_template:
            raise ValueError(f"Position {position.id} has no prompt_template")
        
        # Build context
        context = {
            "position": {
                "id": position.id,
                "type": position.type,
                "title": position.title,
                "jd": position.jd,
                "department": position.department,
                "rules": position.rules,
            },
            "agent": agent,
        }
        
        # Add extra context if provided
        if extra_context:
            context.update(extra_context)
        
        # Render template
        template = self.env.from_string(position.prompt_template)
        return template.render(**context)
    
    def render_with_defaults(
        self,
        position: Position,
        agent_name: str,
        agent_personality: str,
        extra_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """Render with standard agent fields.
        
        Convenience method for simple cases.
        
        Args:
            position: Position with prompt_template
            agent_name: Agent display name
            agent_personality: Agent personality traits
            extra_context: Additional context
            
        Returns:
            Rendered prompt string
        """
        agent = {
            "name": agent_name,
            "personality": agent_personality,
        }
        return self.render(position, agent, extra_context)
    
    def render_string(self, template_string: str, context: Dict[str, Any]) -> str:
        """Render a template string directly.
        
        Args:
            template_string: Jinja2 template string
            context: Context data for rendering
            
        Returns:
            Rendered string
        """
        template = self.env.from_string(template_string)
        return template.render(**context)
