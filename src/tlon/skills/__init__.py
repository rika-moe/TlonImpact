"""Skill system for Tlon.

This module implements the Agent Skills standard for Tlon:
- Discovery: Scan skills/ directory and load metadata from frontmatter
- Activation: Load full SKILL.md content on demand
- Execution: Load resources and execute scripts

Example:
    >>> from tlon.skills import SkillManager, SkillMetadata
    >>> 
    >>> # Initialize manager
    >>> manager = SkillManager("./skills")
    >>> 
    >>> # Discover available skills
    >>> skills = manager.discover_skills()
    >>> for skill in skills:
    ...     print(f"Found: {skill.name} - {skill.description}")
    
    >>> # Activate a skill
    >>> content = manager.activate_skill("csv-pipeline")
    >>> 
    >>> # Load a resource
    >>> ref = manager.load_resource("csv-pipeline", "references/pandas-api.md")
    >>> 
    >>> # Deactivate when done
    >>> manager.deactivate_skill("csv-pipeline")
"""

from .manager import SkillManager
from .models import Skill, SkillMetadata, SkillParser

__all__ = [
    "SkillManager",
    "Skill",
    "SkillMetadata",
    "SkillParser",
]
