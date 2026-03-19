"""Create Task tool."""

from typing import TYPE_CHECKING

from tlon.subsystem.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.subsystem.tools.tasks.manager import TaskManager


class CreateTaskTool(Tool):
    """Tool to create a new task.

    Use this tool when you need to break down a complex task into smaller,
    manageable pieces. Each task represents one step in your work.

    Example:
        >>> tool = CreateTaskTool(task_manager)
        >>> result = await tool.execute(
        ...     title="Design login API",
        ...     description="Define endpoints for login/logout"
        ... )
    """

    def __init__(self, task_manager: "TaskManager"):
        """Initialize the tool.

        Args:
            task_manager: Task manager for task operations
        """
        super().__init__(
            name="createTask",
            description="Create a new task. Use this to break down complex work into smaller, manageable tasks. Each task should represent one clear step or deliverable.",
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short, clear title describing what needs to be done. Example: 'Design login API endpoints'",
                    },
                    "description": {
                        "type": "string",
                        "description": "Optional detailed description of the task. Include specific requirements, acceptance criteria, or notes.",
                        "default": "",
                    },
                },
                "required": ["title"],
            },
        )
        self.task_manager = task_manager

    async def execute(self, title: str, description: str = "") -> ToolResult:
        """Create a new task.

        Args:
            title: Task title
            description: Optional task description

        Returns:
            ToolResult with created task info
        """
        try:
            task = self.task_manager.create_task(title, description)

            return ToolResult(
                success=True,
                data={
                    "task_id": task.id,
                    "title": task.title,
                    "status": task.status.value,
                },
                message=f"✅ 任务已创建: {task.title} ({task.id})",
            )

        except Exception as e:
            return ToolResult(success=False, data={}, error=f"Failed to create task: {str(e)}")
