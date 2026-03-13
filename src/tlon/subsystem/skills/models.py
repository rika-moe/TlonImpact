"""Data models for Skill system."""

import re
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SkillMetadata:
    """Skill metadata from frontmatter (Discovery phase).
    
    This is the lightweight metadata used for initial discovery,
    without loading the full SKILL.md content.
    
    Attributes:
        name: Skill name (from frontmatter)
        description: Skill description (from frontmatter)
        root_path: Relative path to skill directory (e.g., "./skills/csv-pipeline")
        metadata: Optional metadata dict (emoji, requirements, etc.)
    """
    
    name: str
    description: str
    root_path: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_table_row(self) -> str:
        """Format as a table row for system prompt."""
        # Truncate description if too long
        desc = self.description
        if len(desc) > 50:
            desc = desc[:47] + "..."
        return f"│ {self.name:<15} │ {desc:<50} │ {self.root_path:<24} │"
    
    @classmethod
    def get_table_header(cls) -> str:
        """Get table header for system prompt."""
        return (
            "┌─────────────────┬──────────────────────────────────────────────────────┬──────────────────────────┐\n"
            "│ 技能名称        │ 描述                                                 │ 根路径                   │\n"
            "├─────────────────┼──────────────────────────────────────────────────────┼──────────────────────────┤"
        )
    
    @classmethod
    def get_table_footer(cls) -> str:
        """Get table footer."""
        return (
            "└─────────────────┴──────────────────────────────────────────────────────┴──────────────────────────┘"
        )


@dataclass
class Skill:
    """Complete skill with loaded content (Activation phase).
    
    This represents a fully loaded skill with its SKILL.md content.
    The content is only loaded when activate_skill() is called.
    
    Attributes:
        metadata: Skill metadata
        content: Full SKILL.md content (markdown body)
        is_active: Whether the skill is currently active
    """
    
    metadata: SkillMetadata
    content: str = ""
    is_active: bool = False
    
    def activate(self) -> str:
        """Activate the skill and return its content.
        
        Returns:
            Full SKILL.md content
        """
        self.is_active = True
        return self.content
    
    def deactivate(self) -> None:
        """Deactivate the skill."""
        self.is_active = False


class SkillParser:
    """Parser for SKILL.md files.
    
    Parses frontmatter (YAML) and markdown content separately.
    """
    
    FRONTMATTER_PATTERN = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)
    
    @classmethod
    def parse_file(cls, file_path: Path) -> tuple[Dict[str, Any], str]:
        """Parse a SKILL.md file into frontmatter and content.
        
        Args:
            file_path: Path to SKILL.md file
            
        Returns:
            Tuple of (frontmatter_dict, markdown_content)
            
        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If frontmatter is invalid
        """
        if not file_path.exists():
            raise FileNotFoundError(f"SKILL.md not found: {file_path}")
        
        content = file_path.read_text(encoding='utf-8')
        return cls.parse_content(content)
    
    @classmethod
    def parse_content(cls, content: str) -> tuple[Dict[str, Any], str]:
        """Parse SKILL.md content into frontmatter and markdown body.
        
        Args:
            content: Raw SKILL.md content
            
        Returns:
            Tuple of (frontmatter_dict, markdown_content)
        """
        match = cls.FRONTMATTER_PATTERN.match(content)
        
        if not match:
            # No frontmatter - treat all as markdown content
            return {}, content.strip()
        
        frontmatter_text = match.group(1)
        markdown_content = content[match.end():].strip()
        
        # Parse YAML frontmatter
        frontmatter = cls._parse_yaml(frontmatter_text)
        
        return frontmatter, markdown_content
    
    @classmethod
    def _parse_yaml(cls, text: str) -> Dict[str, Any]:
        """Simple YAML parser for frontmatter.
        
        Supports:
        - Simple key: value pairs
        - Nested objects (one level)
        - JSON strings as values
        
        Args:
            text: YAML text
            
        Returns:
            Parsed dictionary
        """
        result = yaml.safe_load(text)
        print("yaml result = ", result)
        return result

    @classmethod
    def _parse_yaml_value(cls, value: str) -> Any:
        """Parse a YAML value.
        
        Args:
            value: Raw value string
            
        Returns:
            Parsed value (str, int, float, bool, dict, list)
        """
        value = value.strip()
        
        if not value:
            return None
        
        # Try JSON parsing (for complex values like {"key": "value"})
        if value.startswith('{') or value.startswith('['):
            try:
                import json
                return json.loads(value)
            except json.JSONDecodeError:
                pass
        
        # Boolean values
        if value.lower() in ('true', 'yes', 'on'):
            return True
        if value.lower() in ('false', 'no', 'off'):
            return False
        
        # Null values
        if value.lower() in ('null', '~', ''):
            return None
        
        # Numeric values
        try:
            if '.' in value:
                return float(value)
            return int(value)
        except ValueError:
            pass
        
        # Quoted strings (remove quotes)
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            return value[1:-1]
        
        # Default: return as string
        return value
    
    @classmethod
    def parse_skill(cls, skill_dir: Path, root_path_prefix: str = "./skills") -> Skill:
        """Parse a complete skill from its directory.
        
        Args:
            skill_dir: Path to skill directory (e.g., /path/to/skills/csv-pipeline)
            root_path_prefix: Prefix for root_path (e.g., "./skills")
            
        Returns:
            Skill object with metadata and content
        """
        skill_md_path = skill_dir / "SKILL.md"
        
        frontmatter, content = cls.parse_file(skill_md_path)
        print("frontmatter=", frontmatter)
        
        # Extract required fields
        name = frontmatter.get('name', skill_dir.name)
        description = frontmatter.get('description', '')
        metadata = frontmatter.get('metadata', {})
        
        # Build root path (relative to project root)
        root_path = f"{root_path_prefix}/{skill_dir.name}"
        
        skill_metadata = SkillMetadata(
            name=name,
            description=description,
            root_path=root_path,
            metadata=metadata
        )
        
        return Skill(
            metadata=skill_metadata,
            content=content,
            is_active=False
        )
