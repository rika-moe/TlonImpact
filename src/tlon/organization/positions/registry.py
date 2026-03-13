"""Position Registry for managing position templates.

This module provides PositionRegistry which manages all available positions
and provides lookup and management capabilities.
"""

from pathlib import Path
from typing import Dict, List, Optional
import yaml

from .models import Position
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
    
    def __init__(self):
        """Initialize empty position registry."""
        self._positions: Dict[str, Position] = {}
        self._loaded_paths: List[Path] = []
    
    def register(self, position: Position) -> None:
        """Register a position.
        
        Args:
            position: Position to register
        """
        self._positions[position.id] = position
        logger.debug(f"Registered position: {position.id} ({position.title})")
    
    def get(self, position_id: str) -> Optional[Position]:
        """Get a position by id.
        
        Args:
            position_id: Position identifier
            
        Returns:
            Position or None if not found
        """
        return self._positions.get(position_id)
    
    def get_by_type(self, position_type: str) -> Optional[Position]:
        """Get a position by type.
        
        Args:
            position_type: Position type code
            
        Returns:
            Position or None if not found
        """
        for position in self._positions.values():
            if position.type == position_type:
                return position
        return None
    
    def get_all(self) -> List[Position]:
        """Get all registered positions.
        
        Returns:
            List of all positions
        """
        return list(self._positions.values())
    
    def get_by_department(self, department: str) -> List[Position]:
        """Get positions by department.
        
        Args:
            department: Department name
            
        Returns:
            List of positions in the department
        """
        return [p for p in self._positions.values() if p.department == department]
    
    def load_from_file(self, path: Path) -> Optional[Position]:
        """Load a position from a YAML file.
        
        Args:
            path: Path to YAML file
            
        Returns:
            Loaded position or None if failed
        """
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            if not data or 'position' not in data:
                logger.warning(f"No 'position' key found in {path}")
                return None
            
            position_data = data['position']
            position = Position.from_dict(position_data)
            self.register(position)
            self._loaded_paths.append(path)
            
            logger.info(f"Loaded position from {path}: {position.id}")
            return position
            
        except Exception as e:
            logger.error(f"Failed to load position from {path}: {e}")
            return None
    
    def load_from_directory(self, directory: Path) -> List[Position]:
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
    
    def has_position(self, position_id: str) -> bool:
        """Check if a position exists.
        
        Args:
            position_id: Position identifier
            
        Returns:
            True if position exists
        """
        return position_id in self._positions
    
    def clear(self) -> None:
        """Clear all registered positions."""
        self._positions.clear()
        self._loaded_paths.clear()
        logger.debug("Position registry cleared")
