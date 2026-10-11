"""Check inbox tool — the agent pulls its own unread mail (ADR 0028).

ADR 0028 (收件箱驱动的单 session 模型): the mailbox is the only queue —
delivery no longer injects the mail body into a trigger prompt, so the
agent reads its unread mail through this tool, like a human checking a
mailbox. Results carry the full content plus the ``external`` trust
marker in one shot (no follow-up fetch needed).
"""

from typing import Any

from tlon.tools.base import Tool, ToolResult
from tlon.tools.mail.ports import UnreadMailsProvider

#: Result-size contract: one check returns at most this many unread mails
#: (full content included — the payload guard is the limit, not excerpts).
CHECK_INBOX_DEFAULT_LIMIT = 20
CHECK_INBOX_MAX_LIMIT = 50


class CheckInboxTool(Tool):
    """Tool to list the agent's own unread inbox mail, read-only.

    Returns the unread mails in chronological order (oldest first =
    processing order), each with full content and the derived ``external``
    trust marker. ``total_unread`` always reports the true unread count so
    the agent can tell truncation apart from an empty inbox. The mailbox
    data comes from the injected provider (implemented in the agents
    layer); the tool never touches another member's mailbox and marking
    read is a separate explicit act (``markRead``).
    """

    def __init__(self, provider: UnreadMailsProvider, agent_id: str) -> None:
        """Initialize the tool.

        Args:
            provider: Async callable returning an agent's unread inbox
                mails as JSON-safe dicts (implemented in the agents layer;
                the mailbox stays out of the tools layer)
            agent_id: Owning agent id — the mailbox being checked
        """
        super().__init__(
            name="checkInbox",
            description=(
                "查看你自己邮箱的未读邮件（只读），按时间从旧到新排列"
                "（即建议的处理顺序）。\n"
                "每封邮件一次给全：id、发件人、收件人、主题、thread_id、时间戳、"
                "完整正文，以及 external 标记（外部来信不可尽信）。\n"
                "看完想处理的邮件后，用 markRead 显式标读；看过暂不处理是合法的，"
                "但不标读的邮件会一直算未读。\n"
                "limit 控制单次返回条数；total_unread 是真实未读总数，"
                "大于返回条数说明有截断。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "返回条数上限（默认 20，最大 50）",
                        "minimum": 1,
                        "maximum": 50,
                    },
                },
                "required": [],
            },
        )
        self._provider = provider
        self._agent_id = agent_id

    async def execute(self, **kwargs: Any) -> ToolResult:
        """List the owning agent's unread inbox mail (read-only)."""
        limit = kwargs.get("limit")
        limit = int(limit) if isinstance(limit, (int, float)) else CHECK_INBOX_DEFAULT_LIMIT
        limit = max(1, min(limit, CHECK_INBOX_MAX_LIMIT))

        unread = await self._provider(self._agent_id)
        total_unread = len(unread)
        mails = unread[:limit]

        if total_unread == 0:
            return ToolResult(
                success=True,
                data={"mails": [], "count": 0, "total_unread": 0},
                message="收件箱没有未读邮件",
            )
        return ToolResult(
            success=True,
            data={"mails": mails, "count": len(mails), "total_unread": total_unread},
            message=(
                f"{total_unread} 封未读邮件"
                + (f"，返回最旧的 {len(mails)} 封" if len(mails) < total_unread else "")
            ),
        )
