"""Remove Task tool."""

from typing import TYPE_CHECKING

from .base import Tool, ToolResult

if TYPE_CHECKING:
    from ..tasks.manager import TaskManager


class RemoveTaskTool(Tool):
    """Tool to remove a completed task.
    
    Use this tool to clean up finished tasks and keep your task list
    manageable. Only completed tasks can be removed. This action is
    permanent and cannot be undone.
    
    Example:
        >>> tool = RemoveTaskTool(task_manager)
        >>> result = await tool.execute(task_id="t-001")
    """
    
    def __init__(self, task_manager: "TaskManager"):
        """Initialize the tool.
        
        Args:
            task_manager: Task manager for task operations
        """
        super().__init__(
            name="removeTask",
            description="Remove a completed task from the list. Use this to clean up finished tasks and keep your list manageable. Only 'done' tasks can be removed. This action is permanent.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The task ID to remove. Example: 't-001'"
                    }
                },
                "required": ["task_id"]
            }
        )
        self.task_manager = task_manager
    
    async def execute(self, task_id: str) -> ToolResult:
        """Remove a task.
        
        Args:
            task_id: Task ID to remove
            
        Returns:
            ToolResult with removed task info
        """
        try:
            task = self.task_manager.get_task(task_id)
            
            if not task:
                return ToolResult(
                    success=False,
                    data={},
                    error=f"Task not found: {task_id}"
                )
            
            if task.status.value != "done":
                return ToolResult(
                    success=False,
                    data={},
                    error=f"Cannot remove task with status '{task.status.value}'. Only completed tasks can be removed. Use completeTask first."
                )
            
            removed_task = self.task_manager.remove_task(task_id)
            
            if removed_task:
                return ToolResult(
                    success=True,
                    data={
                        "task_id": removed_task.id,
                        "title": removed_task.title,
                    },
                    message=f"🗑️ 已移除任务: {removed_task.title}"
                )
            
            return ToolResult(
                success=False,
                data={},
                error="Failed to remove task"
            )
            
        except Exception as e:
            return ToolResult(
                success=False,
                data={},
                error=f"Failed to remove task: {str(e)}"
            )