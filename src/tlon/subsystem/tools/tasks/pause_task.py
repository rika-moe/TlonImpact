"""Pause Task tool."""

from typing import TYPE_CHECKING

from tlon.subsystem.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.subsystem.tools.tasks.manager import TaskManager


class PauseTaskTool(Tool):
    """Tool to pause a task.

    Use this tool when you need to temporarily stop working on a task
    to handle something else. Record your current progress so you can
    resume later.

    Example:
        >>> tool = PauseTaskTool(task_manager)
        >>> result = await tool.execute(
        ...     task_id="t-001",
        ...     reason="User asked to fix urgent bug"
        ... )
    """

    def __init__(self, task_manager: "TaskManager"):
        """Initialize the tool.

        Args:
            task_manager: Task manager for task operations
        """
        super().__init__(
            name="pauseTask",
            description="暂停一个任务。需要临时中断当前任务时使用，记录进度以便后续恢复。仅 'doing' 状态的任务可暂停。",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "要暂停的任务 ID，示例：'t-001'",
                    },
                    "reason": {
                        "type": "string",
                        "description": "可选的暂停原因，记录当前进度和中断原因，便于恢复时参考。",
                        "default": "",
                    },
                },
                "required": ["task_id"],
            },
        )
        self.task_manager = task_manager

    async def execute(self, task_id: str, reason: str = "") -> ToolResult:
        """Pause a task.

        Args:
            task_id: Task ID to pause
            reason: Optional reason/progress note

        Returns:
            ToolResult with paused task info
        """
        try:
            task = self.task_manager.get_task(task_id)

            if not task:
                return ToolResult(success=False, data={}, error=f"Task not found: {task_id}")

            if task.status.value != "doing":
                return ToolResult(
                    success=False,
                    data={},
                    error=f"Cannot pause task with status '{task.status.value}'. Only 'doing' tasks can be paused.",
                )

            paused_task = self.task_manager.pause_task(task_id)

            if paused_task:
                # Update description with progress note if provided
                if reason:
                    paused_task.description = (
                        f"{paused_task.description}\n[Paused] {reason}".strip()
                    )
                    self.task_manager.save()

                return ToolResult(
                    success=True,
                    data={
                        "task_id": paused_task.id,
                        "title": paused_task.title,
                        "status": paused_task.status.value,
                        "pause_count": paused_task.pause_count,
                    },
                    message=f"⏸️ 已暂停任务: {paused_task.title}\n当前进度: {reason or '未记录'}",
                )

            return ToolResult(success=False, data={}, error="Failed to pause task")

        except Exception as e:
            return ToolResult(success=False, data={}, error=f"Failed to pause task: {str(e)}")
