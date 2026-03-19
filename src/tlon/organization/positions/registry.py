"""Position Registry for managing position templates.

This module provides PositionRegistry which manages all available positions
and provides lookup and management capabilities.
"""

from pathlib import Path

from tlon.base.config.loader import ConfigLoader
from tlon.base.config.models import PositionConfig
from tlon.base.config.saver import ConfigSaver
from tlon.base.logger import get_logger

logger = get_logger(__name__)


class PositionRegistry:
    """Registry for managing position templates.

    The registry loads and manages all position configurations,
    providing lookup by id or type.

    Example:
        >>> registry = PositionRegistry()
        >>> registry.load_from_directory(Path("config/positions"))
        >>> developer = registry.get("developer")
        >>> all_tech = registry.get_by_department("技术部")
    """

    def __init__(self, positions_dir: Path | None = None):
        """Initialize position registry.

        Args:
            positions_dir: Directory containing position YAML files
        """
        self._positions: dict[str, PositionConfig] = {}
        self._loaded_paths: list[Path] = []

        # Use ConfigLoader and ConfigSaver for actual operations
        positions_dir_str = str(positions_dir) if positions_dir else None
        self._loader = ConfigLoader(positions_dir=positions_dir_str)
        self._saver = ConfigSaver(positions_dir=positions_dir_str)

    def register(self, position: PositionConfig) -> None:
        """Register a position.

        Args:
            position: Position to register
        """
        self._positions[position.id] = position
        logger.debug(f"Registered position: {position.id} ({position.title})")

    def get(self, position_id: str) -> PositionConfig | None:
        """Get a position by id.

        Args:
            position_id: Position identifier

        Returns:
            PositionConfig or None if not found
        """
        # First check cache, then try loading
        if position_id not in self._positions:
            position = self._loader.load_position(position_id)
            if position:
                self._positions[position_id] = position
        return self._positions.get(position_id)

    def get_by_type(self, position_type: str) -> PositionConfig | None:
        """Get a position by type.

        Args:
            position_type: Position type code

        Returns:
            PositionConfig or None if not found
        """
        for position in self._positions.values():
            if position.type == position_type:
                return position
        # Try loading from file
        position = self._loader.load_position(position_type)
        if position:
            self._positions[position.id] = position
            return position
        return None

    def get_all(self) -> list[PositionConfig]:
        """Get all registered positions.

        Returns:
            List of all positions
        """
        return list(self._positions.values())

    def get_by_department(self, department: str) -> list[PositionConfig]:
        """Get positions by department.

        Args:
            department: Department name

        Returns:
            List of positions in the department
        """
        return [p for p in self._positions.values() if p.department == department]

    def load_from_file(self, path: Path) -> PositionConfig | None:
        """Load a position from a YAML file.

        Args:
            path: Path to YAML file

        Returns:
            Loaded position or None if failed
        """
        try:
            # Use ConfigLoader for loading
            position = self._loader._load_position_file(path)
            if position:
                self._positions[position.id] = position
                self._loaded_paths.append(path)
                logger.info(f"Loaded position from {path}: {position.id}")
                return position
        except Exception as e:
            logger.error(f"Failed to load position from {path}: {e}")
        return None

    def load_from_directory(self, directory: Path) -> list[PositionConfig]:
        """Load all positions from a directory.

        Scans directory for YAML files and loads position configurations.

        Args:
            directory: Directory containing position YAML files

        Returns:
            List of loaded positions
        """
        if not directory.exists():
            logger.warning(f"Position directory not found: {directory}")
            return []

        loaded = []
        for yaml_file in sorted(directory.glob("*.yaml")):
            # Skip files starting with underscore (private templates)
            if yaml_file.name.startswith("_"):
                continue

            position = self.load_from_file(yaml_file)
            if position:
                loaded.append(position)

        logger.info(f"Loaded {len(loaded)} positions from {directory}")
        return loaded

    def load_all(self) -> dict[str, PositionConfig]:
        """Load all positions using ConfigLoader.

        Returns:
            Dictionary mapping position id to PositionConfig
        """
        positions = self._loader.load_all_positions()
        self._positions.update(positions)
        return positions

    def has_position(self, position_id: str) -> bool:
        """Check if a position exists.

        Args:
            position_id: Position identifier

        Returns:
            True if position exists
        """
        return self.get(position_id) is not None

    def clear(self) -> None:
        """Clear all registered positions."""
        self._positions.clear()
        self._loaded_paths.clear()
        self._loader.clear_cache()
        logger.debug("Position registry cleared")


__all__ = ["PositionRegistry"]
