"""Reputation tool — a member's derived reputation report ()."""

from typing import Any

from tlon.tools.base import Tool, ToolResult
from tlon.tools.observability.ports import ReputationProvider


class GetReputationTool(Tool):
    """Tool to read a member's reputation, derived from mail behavior.

    Strictly read-only: reputation is recomputed from the society's
    mail files on every call and never stored. In the Republic of
    Letters a scholar's reputation is public — any member id can be
    queried, not just the caller's own.
    """

    def __init__(self, provider: ReputationProvider, agent_id: str) -> None:
        """Initialize the tool.

        Args:
            provider: Async callable deriving a member's reputation
                (implemented in the agents layer; mailboxes stay out of
                the tools layer)
            agent_id: Owning agent id — the default query subject
        """
        super().__init__(
            name="getReputation",
            description=(
                "查询成员的声誉报告（只读）。声誉不存储、不投票，"
                "每次调用都从全社会的邮件行为数据实时派生：\n"
                "- endorsements_given / endorsements_received：该成员发出/收到的致谢数"
                "（致谢 = 邮件带 endorsing 字段，表示「这封信帮到了我」）。\n"
                "- top_endorsed_mails：被最多不同成员致谢的邮件 top 5。\n"
                "- mail_behavior：收发数、回复率、平均首次回复时延。\n"
                "典型用途：评审原型提案时引用提案者的历史记录作为证据；"
                "书信共和国里学者声誉是公开的，可查任何成员。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": (
                            "可选。要查询的成员 id（agent 用裸 id，人类用 "
                            "'human:<username>'）；留空则查询你自己。"
                        ),
                    },
                },
                "required": [],
            },
        )
        self._provider = provider
        self._agent_id = agent_id

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Return the queried member's derived reputation (read-only)."""
        subject = kwargs.get("agent_id") or self._agent_id
        reputation = await self._provider(subject)
        return ToolResult(
            success=True,
            data=reputation,
            message=(
                f"声誉报告（{subject}）：发出致谢 "
                f"{reputation.get('endorsements_given', 0)} 次，"
                f"收到致谢 {reputation.get('endorsements_received', 0)} 次"
            ),
        )
