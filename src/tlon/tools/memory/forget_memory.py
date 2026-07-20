"""Tool for forgetting/deleting memories."""

from typing import TYPE_CHECKING

from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.memory.manager import MemoryManager

logger = get_logger(__name__)


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
            description="删除长期记忆中的摘要或事实",
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

    async def execute(
        self,
        query: str,
        confirm: bool = False,
    ) -> ToolResult:
        """Execute the tool to forget memories.

        Args:
            query: Description of what to forget
            confirm: If True, actually delete; if False, just preview

        Returns:
            ToolResult with result message
        """
        try:
            result = await self.memory.forget(query, confirm)

            return ToolResult(
                success=True,
                data={"result": result},
                message=result,
            )
        except Exception as e:
            logger.error(f"Error forgetting memory: {e}")
            return ToolResult(
                success=False,
                error=str(e),
                message=f"❌ 删除记忆失败: {e}",
            )
