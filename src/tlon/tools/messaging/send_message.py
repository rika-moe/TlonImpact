"""Send message tool."""

from tlon.tools.base import Tool, ToolResult


class SendMessageTool(Tool):
    """Tool to send an intermediate message to the chatroom.

    Unlike completeDialog (which ends the thinking loop), sendMessage
    sends a progress update or intermediate result while the agent
    continues thinking. The Agent's ReAct loop detects this tool call
    and forwards the message via EventBus without terminating the loop.

    Example:
        >>> tool = SendMessageTool()
        >>> result = await tool.execute(
        ...     content="正在分析中，已发现3个问题...",
        ...     mentions=["人类"]
        ... )
    """

    def __init__(self):
        """Initialize the tool."""
        super().__init__(
            name="sendMessage",
            description=(
                "发送中间消息到聊天室。与 completeDialog 不同，此工具不会结束思考循环，"
                "适用于在长时间思考过程中发送进度报告、阶段性发现或需要用户确认的信息。"
                "发送后 Agent 会继续执行后续工具调用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "消息内容",
                    },
                    "mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选。需要@提及的用户ID或名字列表",
                        "default": [],
                    },
                },
                "required": ["content"],
            },
        )

    async def execute(self, content: str, mentions: list[str] | None = None) -> ToolResult:
        """Send a message.

        The actual message delivery is handled by the Agent's processing
        loop when it detects this tool was called.

        Args:
            content: Message content.
            mentions: Optional list of users to mention.

        Returns:
            ToolResult containing the message data.
        """
        return ToolResult(
            success=True,
            data={
                "content": content,
                "mentions": mentions or [],
                "action": "send_message",
            },
            message=f"💬 {content[:50]}..." if len(content) > 50 else f"💬 {content}",
        )
