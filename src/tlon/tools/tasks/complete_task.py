"""Complete Task tool."""

from typing import TYPE_CHECKING, Any

from tlon.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.tools.tasks.manager import TaskManager


class CompleteTaskTool(Tool):
    """Tool to mark a task as completed.

    Use this tool when you finish a task. Record what was accomplished
    so you can reference it later. The task duration will be calculated
    automatically.

    Example:
        >>> tool = CompleteTaskTool(task_manager)
        >>> result = await tool.execute(
        ...     task_id="t-001",
        ...     result="Implemented JWT authentication with refresh tokens"
        ... )
    """

    def __init__(self, task_manager: "TaskManager"):
        """Initialize the tool.

        Args:
            task_manager: Task manager for task operations
        """
        super().__init__(
            name="completeTask",
            description="将任务标记为已完成。任务结束时调用，记录完成内容以供后续参考。任务耗时自动计算。",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "要完成的任务 ID，示例：'t-001'",
                    },
                    "result": {
                        "type": "string",
                        "description": "完成情况摘要，记录关键交付物、输出结果或发现，便于后续追踪和参考。",
                        "default": "",
                    },
                },
                "required": ["task_id"],
            },
        )
        self.task_manager = task_manager

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Complete a task.

        Args:
            task_id: Task ID to complete
            result: Optional result summary

        Returns:
            ToolResult with completed task info
        """
        task_id: str = kwargs["task_id"]
        result: str = kwargs.get("result", "")

        task = self.task_manager.get_task(task_id)

        if not task:
            return ToolResult(success=False, data={}, error=f"Task not found: {task_id}")

        if task.status.value == "done":
            return ToolResult(success=False, data={}, error=f"Task {task_id} is already completed")

        if task.status.value == "todo":
            return ToolResult(
                success=False,
                data={},
                error="Cannot complete task that hasn't been started. Use startTask first.",
            )

        completed_task = self.task_manager.complete_task(task_id, result)

        if completed_task:
            duration = completed_task.get_duration_minutes()

            return ToolResult(
                success=True,
                data={
                    "task_id": completed_task.id,
                    "title": completed_task.title,
                    "status": completed_task.status.value,
                    "duration_minutes": duration,
                    "pause_count": completed_task.pause_count,
                },
                message=f"✅ 已完成任务: {completed_task.title}"
                + (f" (用时{duration}分钟)" if duration else ""),
            )

        return ToolResult(success=False, data={}, error="Failed to complete task")
