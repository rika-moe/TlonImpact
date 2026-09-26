"""Tool for forgetting/deleting memories."""

from typing import TYPE_CHECKING, Any

from tlon.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.memory.manager import MemoryManager


class ForgetMemoryTool(Tool):
    """Tool to forget/delete memories.

    This tool allows the agent to delete memories that are
    no longer relevant or were stored incorrectly.

    Example:
        >>> tool = ForgetMemoryTool(memory_manager)
        >>> result = await tool.execute(query="old Python preference", confirm=True)
    """

    def __init__(self, memory_manager: "MemoryManager"):
        """Initialize with memory manager.

        Args:
            memory_manager: The MemoryManager instance
        """
        super().__init__(
            name="forget",
            description="删除长期记忆中的记忆条目",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要删除的内容描述，例如：Python偏好、旧的配置",
                    },
                    "confirm": {
                        "type": "boolean",
                        "description": "是否确认删除，默认False先预览",
                    },
                },
                "required": ["query"],
            },
        )
        self.memory = memory_manager

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool to forget memories.

        Args:
            query: Description of what to forget
            confirm: If True, actually delete; if False, just preview

        Returns:
            ToolResult with result message
        """
        query: str = kwargs["query"]
        confirm: bool = kwargs.get("confirm", False)

        result = await self.memory.forget(query, confirm)

        return ToolResult(
            success=True,
            data={"result": result},
            message=result,
        )
