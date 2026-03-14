"""Position (Job Role) management module for Tlon.

This module provides position template management, allowing positions
(job roles) to be defined separately from agents (personnel).

Example:
    >>> from tlon.positions import Position, PositionRegistry
    >>>
    >>> # Load positions from config
    >>> registry = PositionRegistry()
    >>> registry.load_from_directory(Path("config/positions"))
    >>>
    >>> # Get a position
    >>> dev_position = registry.get("developer")
"""

from .models import Position
from .registry import PositionRegistry

__all__ = [
    "Position",
    "PositionRegistry",
]
