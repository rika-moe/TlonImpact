"""Position (Job Role) models for Tlon.

This module re-exports PositionConfig from base.config.models.
Position is kept as an alias for backward compatibility.
"""

# Re-export PositionConfig - single source of truth
from tlon.base.config.models import PositionConfig

# Position is an alias for PositionConfig for backward compatibility
Position = PositionConfig

__all__ = ["Position", "PositionConfig"]
