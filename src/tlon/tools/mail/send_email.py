"""Send email tool."""

from typing import Any

from tlon.tools.base import Tool, ToolResult


class SendEmailTool(Tool):
    """Tool to send a mail to one or more recipients.

    This is the unified mail outlet for agents (docs/design/mail.md).
    Every recipient address lives in the ``tlon.internal`` domain:
    ``<agent-id>@tlon.internal`` for agents, ``<username>@tlon.internal``
    for humans, and ``all@tlon.internal`` for the whole Congress.

    Use ``is_complete`` to control whether the thinking loop terminates:

    - ``is_complete=false`` → send the mail and continue thinking.
    - ``is_complete=true``  → send the final reply and end the loop.

    The Agent's ReAct loop detects this tool call, builds the Mail
    (inheriting ``in_reply_to`` / ``thread_id`` from the session's
    trigger mail), publishes MAIL_NEW, and checks ``is_complete`` to
    decide whether to return.

    Example:
        >>> tool = SendEmailTool()
        >>> result = await tool.execute(
        ...     to=["gu-yanxi@tlon.internal"],
        ...     content="分析完成，结论如下...",
        ...     is_complete=True,
        ... )
    """

    def __init__(self) -> None:
        """Initialize the tool."""
        super().__init__(
            name="sendEmail",
            description=(
                "发送邮件。这是 Agent 唯一的邮件出口。\n"
                "- to：收件人地址列表（必填），如 'gu-yanxi@tlon.internal'；"
                "广播用 'all@tlon.internal'。\n"
                "- is_complete=false：发送中间进度/阶段性发现，发送后 Agent 继续思考。\n"
                "- is_complete=true：发送最终回复，发送后结束本轮思考。\n"
                "注意：当 is_complete=true 时，本轮只能调用 sendEmail，不能同时调用其他工具。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "收件人地址列表，如 ['xie-yunshu@tlon.internal']",
                    },
                    "cc": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选。抄送地址列表",
                        "default": [],
                    },
                    "subject": {
                        "type": "string",
                        "description": "可选。邮件主题",
                    },
                    "content": {
                        "type": "string",
                        "description": "邮件正文",
                    },
                    "is_complete": {
                        "type": "boolean",
                        "description": "是否为最终回复。true=结束本轮思考，false=继续后续工具调用",
                    },
                    "in_reply_to": {
                        "type": "string",
                        "description": "可选。被回复邮件的 id（转发/另起线程时留空）",
                    },
                },
                "required": ["to", "content", "is_complete"],
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Send a mail.

        The actual delivery is handled by the Agent's processing loop
        when it detects this tool was called.

        Args:
            to: Primary recipient addresses.
            content: Mail body.
            is_complete: Whether this mail ends the thinking loop.
            cc: Optional carbon-copy recipients.
            subject: Optional subject.
            in_reply_to: Optional id of the mail being replied to.

        Returns:
            ToolResult containing the mail data.
        """
        to: list[str] = kwargs["to"]
        content: str = kwargs["content"]
        is_complete: bool = kwargs["is_complete"]
        cc: list[str] | None = kwargs.get("cc")
        subject: str | None = kwargs.get("subject")
        in_reply_to: str | None = kwargs.get("in_reply_to")

        return ToolResult(
            success=True,
            data={
                "to": to,
                "cc": cc or [],
                "subject": subject,
                "content": content,
                "is_complete": is_complete,
                "in_reply_to": in_reply_to,
                "action": "send_email",
            },
            message=f"✉️ {content[:50]}..." if len(content) > 50 else f"✉️ {content}",
        )
