"""Forward mail tool — the cognitive member's forwarding outlet (ADR 0029).

ADR 0028 retired the trigger mail, so the DSL ``forward`` action (which
reads the triggering event's mail) has no source on the cognitive path.
Forwarding is a runtime decision — which mail, to whom — so it is carried
by this declarative tool, mirroring :class:`SendEmailTool`: ``execute``
only declares the intent as data; the act stage turns a successful result
into a ``MailEffect`` and ``EffectExecutor`` composes and sends the actual
forward (subject ``Fwd: <原主题>``, self-contained body with an
attribution header, ``forward_of`` linking the original mail).
"""

from typing import Any

from tlon.tools.base import Tool, ToolResult


class ForwardMailTool(Tool):
    """Tool to forward a mail from the agent's own mailbox.

    ``mail_id`` must be a mail that genuinely exists in the agent's own
    mailbox (inbox + sent — the ids checkInbox / searchMails return); the
    engine validates the reference before sending, and a hallucinated id
    fails the send with a postmaster notice — the same hallucination
    guard as ``sendEmail``'s ``in_reply_to``. The forward opens a fresh
    thread (it is not a reply) and never ends the thinking loop: after
    forwarding, the agent keeps thinking.

    Example:
        >>> tool = ForwardMailTool()
        >>> result = await tool.execute(
        ...     mail_id="a1b2c3...",
        ...     to=["gu-yanxi@tlon.internal"],
        ...     comment="这封和你负责的方向相关，转给你看看",
        ... )
    """

    def __init__(self) -> None:
        """Initialize the tool."""
        super().__init__(
            name="forwardMail",
            description=(
                "转发你自己邮箱里的一封邮件给其他成员。\n"
                "- mail_id：被转发邮件的 id（必填）。id 必须来自 checkInbox / searchMails "
                "的返回结果——幻觉 id 会导致发送失败。\n"
                "- to：收件人地址列表（必填）；cc：可选抄送列表。\n"
                "- comment：可选。你自己想说的话，会放在转发的归因头之前。\n"
                "主题自动生成（Fwd: 原主题），正文自动携带原信的完整内容与归因头"
                "（发件人/时间/原主题）；转发不结束本轮思考，发完继续。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "mail_id": {
                        "type": "string",
                        "description": (
                            "被转发邮件的 id。必须是你自己邮箱中真实存在的邮件"
                            "（从 checkInbox / searchMails 结果里取；幻觉 id 会导致发送失败）"
                        ),
                    },
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
                    "comment": {
                        "type": "string",
                        "description": "可选。转发者自己的话，放在归因头之前",
                    },
                },
                "required": ["mail_id", "to"],
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Declare a forward.

        The actual composition (attribution header, ``Fwd:`` subject) and
        delivery are handled by the Agent's processing loop when it
        detects this tool was called.

        Args:
            mail_id: Id of the mail being forwarded.
            to: Primary recipient addresses.
            cc: Optional carbon-copy recipients.
            comment: Optional words of the forwarder, placed before the
                attribution header.

        Returns:
            ToolResult containing the declared forward data.
        """
        mail_id: str = kwargs["mail_id"]
        to: list[str] = kwargs["to"]
        cc: list[str] | None = kwargs.get("cc")
        comment: str | None = kwargs.get("comment")

        return ToolResult(
            success=True,
            data={
                "mail_id": mail_id,
                "to": to,
                "cc": cc or [],
                "comment": comment,
                "action": "forward_mail",
            },
            message=(
                "✉️ 转发已受理，进入投递队列。投递是异步的：若发送或投递失败"
                "（如 mail_id 不在你的邮箱中、收件人地址不存在），稍后会收到 "
                "postmaster 的通知邮件——以该通知为唯一的失败信号，"
                "不要在本轮向用户承诺「已转发」。"
            ),
        )
