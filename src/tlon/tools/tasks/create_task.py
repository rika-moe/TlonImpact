"""Create Task tool."""

from typing import TYPE_CHECKING, Any

from tlon.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.tools.tasks.manager import TaskManager


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
            description="创建新任务。将复杂工作拆解为更小、可管理的任务，每个任务应代表一个清晰的步骤或交付物。",
            parameters={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "简短清晰的任务标题，描述需要完成的事项。示例：'设计登录 API 接口'",
                    },
                    "description": {
                        "type": "string",
                        "description": "可选的详细描述，包含具体需求、验收标准或备注。",
                        "default": "",
                    },
                },
                "required": ["title"],
            },
        )
        self.task_manager = task_manager

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Create a new task.

        Args:
            title: Task title
            description: Optional task description

        Returns:
            ToolResult with created task info
        """
        title: str = kwargs["title"]
        description: str = kwargs.get("description", "")

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
