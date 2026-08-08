"""Tool for compacting working memory into a long-term summary."""

from typing import TYPE_CHECKING, Any

from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.memory.manager import MemoryManager

logger = get_logger(__name__)


class CompactTool(Tool):
    """Tool to compact working memory into a long-term summary.

    This tool allows the agent to explicitly compact all current working
    memory interactions into a long-term summary, optionally focusing on
    specific content via instructions.

    Example:
        >>> tool = CompactTool(memory_manager)
        >>> result = await tool.execute(instructions="保留关于部署方案的决策")
    """

    def __init__(self, memory_manager: "MemoryManager"):
        """Initialize with memory manager.

        Args:
            memory_manager: The MemoryManager instance
        """
        super().__init__(
            name="compact",
            description="压缩当前工作记忆为长期摘要，可用 instructions 指定保留重点",
            parameters={
                "type": "object",
                "properties": {
                    "instructions": {
                        "type": "string",
                        "description": "指定摘要时重点保留的内容，如某个话题或决策",
                    },
                },
            },
        )
        self.memory = memory_manager

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool to compact working memory.

        Args:
            instructions: Optional focus for the summary, e.g. a topic
                or decision to preserve

        Returns:
            ToolResult with the summary ID
        """
        instructions: str | None = kwargs.get("instructions")

        try:
            summary_id = await self.memory.generate_summary(
                force=True, instructions=instructions
            )

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
            logger.error("summary_generation_failed", error=str(e), exc_info=True)
            return ToolResult(
                success=False,
                data={},
                error=str(e),
                message=f"❌ 生成摘要失败: {e}",
            )
