"""List Tasks tool."""

from typing import TYPE_CHECKING, Any

from tlon.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.tools.tasks.manager import TaskManager


class ListTasksTool(Tool):
    """Tool to list all tasks.

    Use this tool to check your current task list and progress.
    You can filter by status to see only specific types of tasks.

    Example:
        >>> tool = ListTasksTool(task_manager)
        >>> result = await tool.execute(filter="doing")
    """

    def __init__(self, task_manager: "TaskManager"):
        """Initialize the tool.

        Args:
            task_manager: Task manager for task operations
        """
        super().__init__(
            name="listTasks",
            description="列出所有任务及其状态。用于查看当前工作量和进度，可按状态筛选。",
            parameters={
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "按状态筛选，可选值：'all'（默认）、'todo'、'doing'、'paused'、'done'",
                        "enum": ["all", "todo", "doing", "paused", "done"],
                        "default": "all",
                    }
                },
            },
        )
        self.task_manager = task_manager

    async def execute(self, **kwargs: Any) -> ToolResult:
        """List tasks.

        Args:
            filter: Status filter (all/todo/doing/paused/done)

        Returns:
            ToolResult with task list
        """
        filter: str = kwargs.get("filter", "all")

        status_filter = None if filter == "all" else filter
        tasks = self.task_manager.list_tasks(status_filter)
        stats = self.task_manager.get_stats()

        # Format tasks for display
        task_list = []
        for task in tasks:
            task_list.append(
                {
                    "id": task.id,
                    "title": task.title,
                    "status": task.status.value,
                }
            )

        return ToolResult(
            success=True,
            data={
                "total": stats["total"],
                "doing": stats["doing"],
                "paused": stats["paused"],
                "todo": stats["todo"],
                "done": stats["done"],
                "tasks": task_list,
            },
            message=f"📋 任务列表 ({stats['total']}个): 进行中{stats['doing']} | 暂停{stats['paused']} | 待处理{stats['todo']} | 已完成{stats['done']}",
        )
