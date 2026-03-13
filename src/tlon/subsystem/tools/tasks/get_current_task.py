"""Get Current Task tool."""

from typing import TYPE_CHECKING

from tlon.subsystem.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.subsystem.tools.tasks.manager import TaskManager


class GetCurrentTaskTool(Tool):
    """Tool to get the currently active task.
    
    Use this tool when you need to check what you're currently working on.
    Returns the task in 'doing' status, or the most recently paused task
    if nothing is currently active.
    
    Example:
        >>> tool = GetCurrentTaskTool(task_manager)
        >>> result = await tool.execute()
    """
    
    def __init__(self, task_manager: "TaskManager"):
        """Initialize the tool.
        
        Args:
            task_manager: Task manager for task operations
        """
        super().__init__(
            name="getCurrentTask",
            description="Get the currently active task. Returns the task currently in progress (doing), or the most recently paused task. Use this to check what you're working on.",
            parameters={
                "type": "object",
                "properties": {}
            }
        )
        self.task_manager = task_manager
    
    async def execute(self) -> ToolResult:
        """Get current task.
        
        Returns:
            ToolResult with current task info
        """
        try:
            task = self.task_manager.get_current_task()
            
            if not task:
                return ToolResult(
                    success=True,
                    data={"has_task": False},
                    message="当前没有进行中的任务"
                )
            
            # Calculate elapsed time if doing
            elapsed = None
            if task.started_at:
                from datetime import datetime
                start = datetime.fromisoformat(task.started_at)
                elapsed = int((datetime.now() - start).total_seconds() / 60)
            
            return ToolResult(
                success=True,
                data={
                    "has_task": True,
                    "task": {
                        "id": task.id,
                        "title": task.title,
                        "description": task.description,
                        "status": task.status.value,
                        "started_at": task.started_at,
                        "elapsed_minutes": elapsed,
                        "pause_count": task.pause_count,
                    }
                },
                message=f"{task.status.value == 'doing' and '🔄' or '⏸️'} 当前任务: {task.title}" +
                        (f" (已进行{elapsed}分钟)" if elapsed else "")
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                data={},
                error=f"Failed to get current task: {str(e)}"
            )