"""Load skill resource tool."""

from tlon.subsystem.tools.base import Tool, ToolResult
from tlon.subsystem.skills.manager import SkillManager


class LoadSkillResourceTool(Tool):
    """Tool to load a resource file from a skill directory.
    
    This tool loads files from a skill's references/, assets/,
    or other subdirectories. Use this after activating a skill
    when you need to access its additional resources.
    
    Example:
        >>> tool = LoadSkillResourceTool(skill_manager)
        >>> result = await tool.execute(
        ...     skillName="csv-pipeline",
        ...     resourceName="references/pandas-api.md"
        ... )
    """
    
    def __init__(self, skill_manager: SkillManager):
        """Initialize the tool.
        
        Args:
            skill_manager: The skill manager instance
        """
        super().__init__(
            name="loadSkillResource",
            description="Load a resource file from a skill directory (references/, assets/, etc.). Use this to access additional documentation or templates from an activated skill.",
            parameters={
                "type": "object",
                "properties": {
                    "skillName": {
                        "type": "string",
                        "description": "The name of the skill (e.g., 'csv-pipeline')"
                    },
                    "resourceName": {
                        "type": "string",
                        "description": "Relative path to the resource within the skill directory (e.g., 'references/guide.md', 'assets/template.txt')"
                    }
                },
                "required": ["skillName", "resourceName"]
            }
        )
        self.skill_manager = skill_manager
    
    async def execute(
        self, 
        skillName: str,  # noqa: N803
        resourceName: str  # noqa: N803
    ) -> ToolResult:
        """Load a resource file from a skill.
        
        Args:
            skillName: Name of the skill
            resourceName: Relative path to the resource
            
        Returns:
            ToolResult with file content or error
        """
        try:
            content = self.skill_manager.load_resource(skillName, resourceName)
            
            return ToolResult(
                success=True,
                data={
                    "skill_name": skillName,
                    "resource_name": resourceName,
                    "content": content,
                    "content_length": len(content),
                }
            )
            
        except FileNotFoundError as e:
            return ToolResult(
                success=False,
                data={},
                error=str(e)
            )
        except ValueError as e:
            return ToolResult(
                success=False,
                data={},
                error=str(e)
            )
        except Exception as e:
            return ToolResult(
                success=False,
                data={},
                error=f"Failed to load resource '{resourceName}' from skill '{skillName}': {str(e)}"
            )
