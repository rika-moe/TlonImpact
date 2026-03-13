"""Deactivate skill tool."""

from tlon.subsystem.tools.base import Tool, ToolResult
from tlon.subsystem.skills.manager import SkillManager


class DeactivateSkillTool(Tool):
    """Tool to deactivate a skill and release its resources.
    
    This tool deactivates a skill, removing its content from the
    agent's active context. This is optional but helps manage
    context window size.
    
    Example:
        >>> tool = DeactivateSkillTool(skill_manager)
        >>> result = await tool.execute(skillName="csv-pipeline")
    """
    
    def __init__(self, skill_manager: SkillManager):
        """Initialize the tool.
        
        Args:
            skill_manager: The skill manager instance
        """
        super().__init__(
            name="deactivateSkill",
            description="Deactivate a skill and release its context resources. Call this when you're done using a skill to free up context space.",
            parameters={
                "type": "object",
                "properties": {
                    "skillName": {
                        "type": "string",
                        "description": "The name of the skill to deactivate (e.g., 'csv-pipeline')"
                    }
                },
                "required": ["skillName"]
            }
        )
        self.skill_manager = skill_manager
    
    async def execute(self, skillName: str) -> ToolResult:  # noqa: N803
        """Deactivate a skill.
        
        Args:
            skillName: Name of the skill to deactivate
            
        Returns:
            ToolResult indicating success or error
        """
        try:
            result = self.skill_manager.deactivate_skill(skillName)
            
            if not result:
                return ToolResult(
                    success=False,
                    data={},
                    error=f"Skill '{skillName}' is not currently active or does not exist."
                )
            
            return ToolResult(
                success=True,
                data={
                    "skill_name": skillName,
                    "status": "deactivated"
                }
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                data={},
                error=f"Failed to deactivate skill '{skillName}': {str(e)}"
            )
