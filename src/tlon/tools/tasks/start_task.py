"""Start Task tool."""

from typing import TYPE_CHECKING, Any

from tlon.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.tools.tasks.manager import TaskManager


class StartTaskTool(Tool):
    """Tool to start or resume a task.

    Use this tool when you begin working on a task. If there's another
    task currently in progress, it will be automatically paused.

    Example:
        >>> tool = StartTaskTool(task_manager)
        >>> result = await tool.execute(task_id="t-001")
    """

    def __init__(self, task_manager: "TaskManager"):
        """Initialize the tool.

        Args:
            task_manager: Task manager for task operations
        """
        super().__init__(
            name="startTask",
            description="开始或恢复一个任务。若当前有其他任务正在进行，会自动暂停。开始处理任务时调用此工具。",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "要开始的任务 ID，示例：'t-001'",
                    }
                },
                "required": ["task_id"],
            },
        )
        self.task_manager = task_manager

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Start a task.

        Args:
            task_id: Task ID to start

        Returns:
            ToolResult with started task info
        """
        task_id: str = kwargs["task_id"]

        started_task, paused_task = self.task_manager.start_task(task_id)

        if not started_task:
            return ToolResult(success=False, data={}, error=f"Task not found: {task_id}")

        data: dict[str, Any] = {
            "task_id": started_task.id,
            "title": started_task.title,
            "status": started_task.status.value,
        }

        message = f"🔄 已开始任务: {started_task.title}"

        if paused_task:
            data["auto_paused"] = {
                "task_id": paused_task.id,
                "title": paused_task.title,
            }
            message = f"⏸️ 自动暂停: {paused_task.title}\n🔄 开始: {started_task.title}"

        return ToolResult(success=True, data=data, message=message)
