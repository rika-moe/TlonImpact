"""Tool for generating summary from working memory."""

from typing import TYPE_CHECKING

from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.memory.manager import MemoryManager

logger = get_logger(__name__)


class GenerateSummaryTool(Tool):
    """Tool to generate summary from working memory.

    This tool allows the agent to explicitly trigger summary generation
    from current working memory interactions.

    Example:
        >>> tool = GenerateSummaryTool(memory_manager)
        >>> result = await tool.execute()
    """

    def __init__(self, memory_manager: "MemoryManager"):
        """Initialize with memory manager.

        Args:
            memory_manager: The MemoryManager instance
        """
        super().__init__(
            name="generateSummary",
            description="将当前工作记忆中的对话生成为长期摘要",
            parameters={
                "type": "object",
                "properties": {
                    "force": {
                        "type": "boolean",
                        "description": "是否强制生成，即使交互数量不足",
                    },
                },
            },
        )
        self.memory = memory_manager

    async def execute(
        self,
        force: bool = True,
    ) -> ToolResult:
        """Execute the tool to generate summary.

        Args:
            force: If True, generate even if not enough interactions

        Returns:
            ToolResult with the summary ID
        """
        try:
            summary_id = await self.memory.generate_summary(force=force)

            if summary_id:
                return ToolResult(
                    success=True,
                    data={"summary_id": summary_id},
                    message=f"✅ 已生成摘要: {summary_id[:8]}...",
                )
            else:
                return ToolResult(
                    success=True,
                    data={},
                    message="⚠️ 没有足够的内容生成摘要",
                )
        except Exception as e:
            logger.error(f"Error generating summary: {e}")
            return ToolResult(
                success=False,
                error=str(e),
                message=f"❌ 生成摘要失败: {e}",
            )
