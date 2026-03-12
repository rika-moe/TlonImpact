"""Position (Job Role) management module for Tlon.

This module provides position template management, allowing positions
(job roles) to be defined separately from agents (personnel).

Example:
    >>> from tlon.positions import Position, PositionRegistry, PromptRenderer
    >>> 
    >>> # Load positions from config
    >>> registry = PositionRegistry()
    >>> registry.load_from_directory(Path("config/positions"))
    >>> 
    >>> # Get a position and render its prompt
    >>> dev_position = registry.get("developer")
    >>> renderer = PromptRenderer()
    >>> prompt = renderer.render_with_defaults(
    ...     position=dev_position,
    ...     agent_name="林知遥",
    ...     agent_personality="细心"
    ... )
"""

from .models import Position
from .prompt_renderer import PromptRenderer
from .registry import PositionRegistry

__all__ = [
    "Position",
    "PositionRegistry",
    "PromptRenderer",
]
