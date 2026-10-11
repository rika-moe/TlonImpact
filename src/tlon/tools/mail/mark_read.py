"""Mark read tool — the agent explicitly manages its read cursor (ADR 0028).

ADR 0028 (已读归 agent 显式管理): read state is not a side effect of
seeing a mail — the agent decides which mails are processed and marks
them read in batch. Mails it leaves unread stay unread (搁置权), which
is what keeps the wake-up/rescan semantics meaningful.
"""

from typing import Any

from tlon.tools.base import Tool, ToolResult
from tlon.tools.mail.ports import MarkReadActor


class MarkReadTool(Tool):
    """Tool to mark the agent's own inbox mails as read, in batch.

    Forgiving per-id semantics: an already-read id is a no-op, and an id
    that is not in the inbox is reported as ``not_found`` without ever
    touching the cursor (the actor looks before it writes). agent_id
    binds at construction, so only the owning agent's mailbox is
    reachable (same precedent as UpdatePersonaTool).
    """

    def __init__(self, actor: MarkReadActor, agent_id: str) -> None:
        """Initialize the tool.

        Args:
            actor: Async callable marking one agent's inbox mails read
                and reporting per-id outcomes (implemented in the agents
                layer; the mailbox stays out of the tools layer)
            agent_id: Owning agent id — the mailbox being marked
        """
        super().__init__(
            name="markRead",
            description=(
                "把你自己收件箱里的邮件标记为已读，支持一次批量传多个 id。\n"
                "已读或收件箱里不存在的 id 不会报错：结果里分 marked（新标读）/"
                "already_read/not_found 三组汇报。\n"
                "看过但暂不处理的邮件可以不标读——未读邮件会留在 checkInbox 里。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "mail_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要标读的邮件 id 列表（来自 checkInbox 结果）",
                    },
                },
                "required": ["mail_ids"],
            },
        )
        self._actor = actor
        self._agent_id = agent_id

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Mark the owning agent's own inbox mails read (idempotent)."""
        raw = kwargs.get("mail_ids")
        if not isinstance(raw, list) or not all(isinstance(m, str) for m in raw):
            return ToolResult(
                success=False,
                data=None,
                error="mail_ids 必须是字符串数组",
            )
        mail_ids = [mid.strip() for mid in raw if mid.strip()]
        if not mail_ids:
            return ToolResult(
                success=False,
                data=None,
                error="mail_ids 不能为空",
            )

        report = await self._actor(self._agent_id, mail_ids)
        marked = report["marked"]
        return ToolResult(
            success=True,
            data=report,
            message=(
                f"标读 {len(marked)} 封"
                + (f"，{len(report['already_read'])} 封本已读" if report["already_read"] else "")
                + (f"，{len(report['not_found'])} 封不在收件箱" if report["not_found"] else "")
            ),
        )
