"""Complete Task tool."""

from typing import TYPE_CHECKING

from tlon.subsystem.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.subsystem.tools.tasks.manager import TaskManager


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
            description="Mark a task as completed. Use this when you finish a task. Record what was accomplished so you can reference it later. Task duration is calculated automatically.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to complete. Example: 't-001'",
                    },
                    "result": {
                        "type": "string",
                        "description": "Summary of what was accomplished. Record key deliverables, outputs, or findings. This helps track progress and reference later.",
                        "default": "",
                    },
                },
                "required": ["task_id"],
            },
        )
        self.task_manager = task_manager

    async def execute(self, task_id: str, result: str = "") -> ToolResult:
        """Complete a task.

        Args:
            task_id: Task ID to complete
            result: Optional result summary

        Returns:
            ToolResult with completed task info
        """
        try:
            task = self.task_manager.get_task(task_id)

            if not task:
                return ToolResult(success=False, data={}, error=f"Task not found: {task_id}")

            if task.status.value == "done":
                return ToolResult(
                    success=False, data={}, error=f"Task {task_id} is already completed"
                )

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

        except Exception as e:
            return ToolResult(success=False, data={}, error=f"Failed to complete task: {str(e)}")
