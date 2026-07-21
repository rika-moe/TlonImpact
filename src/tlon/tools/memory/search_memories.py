"""Tool for searching memories."""

from typing import TYPE_CHECKING

from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.memory.manager import MemoryManager

logger = get_logger(__name__)


class SearchMemoriesTool(Tool):
    """Tool to search agent's memory.

    This tool allows the agent to search for previously stored
    summaries and facts.

    Example:
        >>> tool = SearchMemoriesTool(memory_manager)
        >>> result = await tool.execute(query="Python preferences")
    """

    def __init__(self, memory_manager: "MemoryManager"):
        """Initialize with memory manager.

        Args:
            memory_manager: The MemoryManager instance
        """
        super().__init__(
            name="searchMemories",
            description="搜索长期记忆中的摘要和事实",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，例如：Python、用户偏好、项目配置",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回结果数，默认5",
                    },
                },
                "required": ["query"],
            },
        )
        self.memory = memory_manager

    async def execute(
        self,
        query: str,
        limit: int = 5,
    ) -> ToolResult:
        """Execute the tool to search memories.

        Args:
            query: Search query string
            limit: Maximum number of results

        Returns:
            ToolResult with search results
        """
        try:
            results_text = await self.memory.search_memories(query, limit)

            return ToolResult(
                success=True,
                data={"results": results_text},
                message=results_text,
            )
        except Exception as e:
            logger.error("memory_search_failed", query=query, error=str(e), exc_info=True)
            return ToolResult(
                success=False,
                error=str(e),
                message=f"❌ 搜索记忆失败: {e}",
            )
