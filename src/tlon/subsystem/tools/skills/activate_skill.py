"""Activate skill tool."""

from tlon.subsystem.tools.base import Tool, ToolResult
from tlon.subsystem.skills.manager import SkillManager


class ActivateSkillTool(Tool):
    """Tool to activate a skill and load its SKILL.md content.
    
    This tool activates a skill, loading its full SKILL.md content
    into the agent's context. The skill must have been discovered
    during startup.
    
    Example:
        >>> tool = ActivateSkillTool(skill_manager)
        >>> result = await tool.execute(skillName="csv-pipeline")
        >>> print(result.data["content"])  # Full SKILL.md content
    """
    
    def __init__(self, skill_manager: SkillManager):
        """Initialize the tool.
        
        Args:
            skill_manager: The skill manager instance
        """
        super().__init__(
            name="activateSkill",
            description="Activate a skill and load its SKILL.md content into the context. Call this when you need to use a specific skill.",
            parameters={
                "type": "object",
                "properties": {
                    "skillName": {
                        "type": "string",
                        "description": "The name of the skill to activate (e.g., 'csv-pipeline', 'plan-i')"
                    }
                },
                "required": ["skillName"]
            }
        )
        self.skill_manager = skill_manager
    
    async def execute(self, skillName: str) -> ToolResult:  # noqa: N803
        """Activate a skill.
        
        Args:
            skillName: Name of the skill to activate
            
        Returns:
            ToolResult with skill content or error
        """
        try:
            content = self.skill_manager.activate_skill(skillName)
            
            if content is None:
                # Skill not found
                discovered = [
                    skill.metadata.name 
                    for skill in self.skill_manager._skills.values()
                ]
                
                return ToolResult(
                    success=False,
                    data={},
                    error=f"Skill '{skillName}' not found. Available skills: {', '.join(discovered) if discovered else 'none discovered'}"
                )
            
            # Get metadata for confirmation
            metadata = self.skill_manager.get_metadata(skillName)
            
            return ToolResult(
                success=True,
                data={
                    "skill_name": skillName,
                    "description": metadata.description if metadata else "",
                    "content": content,
                    "content_length": len(content),
                }
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                data={},
                error=f"Failed to activate skill '{skillName}': {str(e)}"
            )
