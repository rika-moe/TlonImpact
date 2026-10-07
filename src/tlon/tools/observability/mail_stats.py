"""Mail stats tool — the agent's own correspondence health report ()."""

from typing import Any

from tlon.tools.base import Tool, ToolResult
from tlon.tools.observability.ports import MailStatsProvider


class GetMailStatsTool(Tool):
    """Tool to read the agent's own mail social statistics.

    Strictly read-only: the injected provider only reads the agent's own
    mailbox (inbox + sent); no other member's mailbox is touched and
    nothing can be written through this tool.
    """

    def __init__(self, provider: MailStatsProvider, agent_id: str) -> None:
        """Initialize the tool.

        Args:
            provider: Async callable computing an agent's mail stats
                (implemented in the agents layer; mailbox stays out of
                the tools layer)
            agent_id: Owning agent id — the stats subject
        """
        super().__init__(
            name="getMailStats",
            description=(
                "读取你自己的邮件社交统计（只读）：按通信对象聚合的收发数、"
                "你发起的线程数与被回复的线程数、平均首次回复时长（秒）、"
                "以及从未回复过你的通信对象列表。\n"
                "典型问题：哪个成员回我最快？谁从没回过我的信？\n"
                "统计基于你自己的邮箱（inbox + sent），不含他人邮箱。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )
        self._provider = provider
        self._agent_id = agent_id

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Return the owning agent's mail social statistics (read-only)."""
        stats = await self._provider(self._agent_id)
        return ToolResult(
            success=True,
            data=stats,
            message=(
                f"邮件统计：发出 {stats.get('total_sent', 0)} 封，"
                f"收到 {stats.get('total_received', 0)} 封，"
                f"通信对象 {len(stats.get('correspondents', {}))} 个"
            ),
        )
