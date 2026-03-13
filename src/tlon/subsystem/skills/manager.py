"""SkillManager for Discovery, Activation and Execution of Skills."""

from pathlib import Path
from typing import Dict, List, Optional

from .models import Skill, SkillMetadata, SkillParser


class SkillManager:
    """Manager for skill lifecycle: Discovery → Activation → Execution.
    
    The SkillManager handles:
    1. **Discovery**: Scan skills/ directory and load metadata from frontmatter
    2. **Activation**: Load full SKILL.md content on demand
    3. **Resource Loading**: Load references/, assets/ files as needed
    4. **Deactivation**: Clean up when skill is no longer needed
    
    Example:
        >>> manager = SkillManager("./skills")
        >>> 
        >>> # Discovery phase
        >>> skills = manager.discover_skills()
        >>> print(f"Found {len(skills)} skills")
        >>> 
        >>> # Activation phase
        >>> content = manager.activate_skill("csv-pipeline")
        >>> print(f"Loaded {len(content)} characters")
        >>> 
        >>> # Execution phase - load resource
        >>> ref_content = manager.load_resource("csv-pipeline", "references/pandas-api.md")
        >>> 
        >>> # Cleanup
        >>> manager.deactivate_skill("csv-pipeline")
    """
    
    def __init__(self, skills_dir: str = "./skills"):
        """Initialize the skill manager.
        
        Args:
            skills_dir: Path to skills directory (relative or absolute)
        """
        self.skills_dir = Path(skills_dir).resolve()
        self._skills: Dict[str, Skill] = {}  # name -> Skill
        self._active_skills: Dict[str, Skill] = {}  # Currently active skills
    
    def discover_skills(self, skill_names: Optional[List[str]] = None) -> List[SkillMetadata]:
        """Discover available skills by scanning the skills directory.
        
        This is the Discovery phase - only loads metadata from frontmatter,
        not the full SKILL.md content.
        
        Args:
            skill_names: Optional list of skill names to filter.
                        If None, discovers all skills.
                        
        Returns:
            List of skill metadata
        """
        discovered = []
        
        if not self.skills_dir.exists():
            return discovered
        
        for item in self.skills_dir.iterdir():
            if not item.is_dir():
                continue
            
            # Skip hidden directories and special directories
            if item.name.startswith('.') or item.name == 'doc':
                continue
            
            skill_md = item / "SKILL.md"
            if not skill_md.exists():
                continue
            
            # Check if filtered
            if skill_names and item.name not in skill_names:
                continue
            
            try:
                skill = SkillParser.parse_skill(item, root_path_prefix="./skills")
                self._skills[skill.metadata.name] = skill
                discovered.append(skill.metadata)
            except Exception as e:
                # Log error but continue with other skills
                print(f"Warning: Failed to parse skill {item.name}: {e}")
        
        return discovered
    
    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a skill by name.
        
        Args:
            name: Skill name
            
        Returns:
            Skill object or None if not found
        """
        return self._skills.get(name)
    
    def get_metadata(self, name: str) -> Optional[SkillMetadata]:
        """Get skill metadata by name.
        
        Args:
            name: Skill name
            
        Returns:
            SkillMetadata or None if not found
        """
        skill = self._skills.get(name)
        return skill.metadata if skill else None
    
    def activate_skill(self, name: str) -> Optional[str]:
        """Activate a skill and return its full content.
        
        This is the Activation phase - loads the full SKILL.md content
        into memory and marks the skill as active.
        
        Args:
            name: Skill name
            
        Returns:
            Full SKILL.md content, or None if skill not found
            
        Raises:
            ValueError: If skill is already active
        """
        skill = self._skills.get(name)
        
        if not skill:
            return None
        
        if skill.is_active:
            # Already active - return content without error
            return skill.content
        
        # Activate and store
        content = skill.activate()
        self._active_skills[name] = skill
        
        return content
    
    def deactivate_skill(self, name: str) -> bool:
        """Deactivate a skill and release its resources.
        
        Args:
            name: Skill name
            
        Returns:
            True if skill was deactivated, False if not found or not active
        """
        skill = self._active_skills.pop(name, None)
        
        if not skill:
            return False
        
        skill.deactivate()
        return True
    
    def load_resource(self, name: str, resource_path: str) -> str:
        """Load a resource file from a skill directory.
        
        This is part of the Execution phase - loads files from
        references/, assets/, or other subdirectories.
        
        Args:
            name: Skill name
            resource_path: Relative path within skill directory
                          (e.g., "references/pandas-api.md")
                          
        Returns:
            File content as string
            
        Raises:
            FileNotFoundError: If skill or resource doesn't exist
            ValueError: If path tries to escape skill directory (../ attack)
        """
        skill = self._skills.get(name)
        
        if not skill:
            raise FileNotFoundError(f"Skill not found: {name}")
        
        # Security: Prevent directory traversal attacks
        # Normalize the path and ensure it stays within skill directory
        skill_root = self.skills_dir / name
        resource_full_path = (skill_root / resource_path).resolve()
        
        # Ensure the resolved path is within the skill directory
        try:
            resource_full_path.relative_to(skill_root.resolve())
        except ValueError:
            raise ValueError(
                f"Invalid resource path: {resource_path}. "
                "Path must be within skill directory."
            )
        
        if not resource_full_path.exists():
            raise FileNotFoundError(
                f"Resource not found: {resource_path} in skill {name}"
            )
        
        if not resource_full_path.is_file():
            raise ValueError(f"Resource is not a file: {resource_path}")
        
        return resource_full_path.read_text(encoding='utf-8')
    
    def get_active_skills(self) -> List[str]:
        """Get list of currently active skill names.
        
        Returns:
            List of active skill names
        """
        return list(self._active_skills.keys())
    
    def is_active(self, name: str) -> bool:
        """Check if a skill is currently active.
        
        Args:
            name: Skill name
            
        Returns:
            True if skill is active
        """
        skill = self._skills.get(name)
        return skill.is_active if skill else False
    
    def get_skills_table_for_prompt(self) -> str:
        """Generate a formatted table of all discovered skills.
        
        This is used to include skill information in system prompts.
        
        Returns:
            Formatted table string
        """
        if not self._skills:
            return "暂无可用技能。"
        
        lines = [
            "可用技能列表:",
            SkillMetadata.get_table_header()
        ]
        
        for skill in self._skills.values():
            lines.append(skill.metadata.to_table_row())
        
        lines.append(SkillMetadata.get_table_footer())
        
        lines.extend([
            "",
            "技能使用流程:",
            '1. 调用 activateSkill(skillName) 激活技能，加载 SKILL.md 到上下文',
            '2. 如需引用技能内文件，调用 loadSkillResource(skillName, resourceName)',
            '3. 执行完成后，调用 deactivateSkill(skillName) 释放资源',
            '4. 脚本执行使用 executeBashCommand，路径格式: ./skills/{skillName}/scripts/xxx.sh'
        ])
        
        return "\n".join(lines)
    
    def get_active_skills_context(self) -> str:
        """Get combined context of all active skills.
        
        Returns:
            Combined SKILL.md content of all active skills
        """
        if not self._active_skills:
            return ""
        
        sections = []
        for name, skill in self._active_skills.items():
            sections.append(f"## Skill: {name}\n\n{skill.content}")
        
        return "\n\n---\n\n".join(sections)
