"""Send email tool."""

from typing import Any

from tlon.tools.base import Tool, ToolResult


class SendEmailTool(Tool):
    """Tool to send a mail to one or more recipients.

    This is the unified mail outlet for agents (docs/design/mail.md).
    Every recipient address lives in the ``tlon.internal`` domain:
    ``<agent-id>@tlon.internal`` for agents, ``<username>@tlon.internal``
    for humans. There are no group addresses (ADR 0024): one-to-many
    communication lists every recipient in ``to`` / ``cc``.

    Use ``is_complete`` to control whether the thinking loop terminates:

    - ``is_complete=false`` → send the mail and continue thinking.
    - ``is_complete=true``  → send the final reply and end the loop.

    The Agent's ReAct loop detects this tool call and declares the
    reply mail: ``to`` / ``in_reply_to`` are exactly what the model
    declared (ADR 0028 — there is no trigger mail to default to), and
    the engine validates the reference against the agent's own mailbox
    — deriving ``thread_id`` from the referenced mail — before
    publishing MAIL_NEW; ``is_complete`` then decides whether the loop
    returns.

    ``endorsing`` marks the mail as an endorsement of another mail
    ("this mail helped me", issue ): the reference is validated
    against the agent's own mailbox like ``in_reply_to`` (hallucinated
    ids and self-endorsement fail the send), and the endorsement feeds
    the derived reputation signal.

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
                "多人就把每个地址逐个列进 to 或 cc（不存在组地址）。\n"
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
                        "description": (
                            "可选。被回复邮件的 id。"
                            "id 必须是你自己邮箱中真实存在的邮件（幻觉 id 会导致发送失败），"
                            "回复将并入该邮件所在的线程。"
                        ),
                    },
                    "endorsing": {
                        "type": "string",
                        "description": (
                            "可选。致谢信号：被致谢邮件的 id——表示「这封邮件帮到了我」。"
                            "id 必须是你自己邮箱中真实存在的邮件（幻觉 id 会导致发送失败），"
                            "且不能是你自己写的邮件（不允许自我致谢）。"
                            "致谢是公开的、机器可读的社会行为：它会进入对方收件箱，"
                            "并累积为你的社会行为记录（声誉从中派生）。"
                            "与 in_reply_to 相互独立：致谢通常同时是回复，但语义不同。"
                        ),
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
            endorsing: Optional id of the mail being endorsed.

        Returns:
            ToolResult containing the mail data.
        """
        to: list[str] = kwargs["to"]
        content: str = kwargs["content"]
        is_complete: bool = kwargs["is_complete"]
        cc: list[str] | None = kwargs.get("cc")
        subject: str | None = kwargs.get("subject")
        in_reply_to: str | None = kwargs.get("in_reply_to")
        endorsing: str | None = kwargs.get("endorsing")

        return ToolResult(
            success=True,
            data={
                "to": to,
                "cc": cc or [],
                "subject": subject,
                "content": content,
                "is_complete": is_complete,
                "in_reply_to": in_reply_to,
                "endorsing": endorsing,
                "action": "send_email",
            },
            message=(
                "✉️ 邮件已受理，进入投递队列。投递是异步的：若发送或投递失败"
                "（如收件人地址不存在），稍后会收到 postmaster 的通知邮件——"
                "以该通知为唯一的失败信号，"
                "不要在本轮向用户承诺「已送达」。"
            ),
        )
