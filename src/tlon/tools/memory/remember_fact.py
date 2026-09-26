"""Tool for recording claims to memory."""

from typing import TYPE_CHECKING, Any

from tlon.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.memory.manager import MemoryManager


class RememberFactTool(Tool):
    """Tool to record a claim to agent's memory.

    This tool allows the agent to remember important information
    about the user, project state, decisions, etc.

    Example:
        >>> tool = RememberFactTool(memory_manager)
        >>> result = await tool.execute(
        ...     fact="User prefers Python over JavaScript",
        ...     importance=0.8
        ... )
    """

    def __init__(self, memory_manager: "MemoryManager"):
        """Initialize with memory manager.

        Args:
            memory_manager: The MemoryManager instance
        """
        super().__init__(
            name="rememberFact",
            description="记录一条重要记忆到长期记忆中",
            parameters={
                "type": "object",
                "properties": {
                    "fact": {
                        "type": "string",
                        "description": "记忆的自然语言描述，例如：用户喜欢使用Python",
                    },
                    "importance": {
                        "type": "number",
                        "description": "重要程度 (0.0-1.0)，默认0.5",
                    },
                },
                "required": ["fact"],
            },
        )
        self.memory = memory_manager

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool to remember a claim.

        Args:
            fact: Natural language description of what to remember
            importance: Importance score (0-1)

        Returns:
            ToolResult with the claim ID
        """
        fact: str = kwargs["fact"]
        importance: float = kwargs.get("importance", 0.5)

        claim_id = await self.memory.remember_fact(
            fact=fact,
            importance=importance,
        )

        return ToolResult(
            success=True,
            data={"claim_id": claim_id},
            message=f"✅ 已记录记忆: {fact[:50]}...",
        )
