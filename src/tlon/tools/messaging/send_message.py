"""Send message tool."""

from tlon.tools.base import Tool, ToolResult


class SendMessageTool(Tool):
    """Tool to send a message to the chatroom.

    This is the unified message outlet for agents. Use ``is_complete``
    to control whether the thinking loop should terminate.

    - ``is_complete=false`` → send a progress update and continue thinking.
    - ``is_complete=true``  → send the final reply and end the loop.

    The Agent's ReAct loop detects this tool call, forwards the message
    via EventBus, and checks ``is_complete`` to decide whether to return.

    Example:
        >>> tool = SendMessageTool()
        >>> # intermediate progress
        >>> result = await tool.execute(
        ...     content="正在分析中，已发现3个问题...",
        ...     is_complete=False,
        ...     mentions=["人类"],
        ... )
        >>> # final reply
        >>> result = await tool.execute(
        ...     content="分析完成，结论如下...",
        ...     is_complete=True,
        ...     mentions=["人类"],
        ... )
    """

    def __init__(self):
        """Initialize the tool."""
        super().__init__(
            name="sendMessage",
            description=(
                "发送消息到聊天室。这是 Agent 唯一的消息出口。\n"
                "- is_complete=false：发送中间进度/阶段性发现，发送后 Agent 继续思考。\n"
                "- is_complete=true：发送最终回复，发送后结束本轮思考。\n"
                "注意：当 is_complete=true 时，本轮只能调用 sendMessage，不能同时调用其他工具。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "消息内容",
                    },
                    "is_complete": {
                        "type": "boolean",
                        "description": "是否为最终回复。true=结束本轮思考，false=继续后续工具调用",
                    },
                    "mentions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选。需要@提及的用户ID或名字列表",
                        "default": [],
                    },
                },
                "required": ["content", "is_complete"],
            },
        )

    async def execute(
        self, content: str, is_complete: bool, mentions: list[str] | None = None
    ) -> ToolResult:
        """Send a message.

        The actual message delivery is handled by the Agent's processing
        loop when it detects this tool was called.

        Args:
            content: Message content.
            is_complete: Whether this message ends the thinking loop.
            mentions: Optional list of users to mention.

        Returns:
            ToolResult containing the message data.
        """
        return ToolResult(
            success=True,
            data={
                "content": content,
                "is_complete": is_complete,
                "mentions": mentions or [],
                "action": "send_message",
            },
            message=f"💬 {content[:50]}..." if len(content) > 50 else f"💬 {content}",
        )
