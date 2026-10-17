"""Social health tool — the society's orphan-mail report ()."""

from typing import Any

from tlon.tools.base import Tool, ToolResult
from tlon.tools.observability.ports import OrphanMailProvider


class GetSocialHealthTool(Tool):
    """Tool to read the society's orphan-mail list.

    Strictly read-only: the injected provider scans mailboxes that
    already exist on disk and creates nothing; the report is derived on
    demand and never stored.
    """

    def __init__(self, provider: OrphanMailProvider) -> None:
        """Initialize the tool.

        Args:
            provider: Async callable running the society-wide orphan
                scan (implemented in the agents layer; mailboxes stay
                out of the tools layer)
        """
        super().__init__(
            name="getSocialHealth",
            description=(
                "查询全社会的孤儿邮件清单（只读）：当前有哪些发出多天仍无回复的"
                "邮件线程（orphan = 发出 orphan_mail_days 天无人回复的线程根邮件，"
                "自己的跟帖不算回复）。\n"
                "返回 scanned_members（成功扫描的公开成员 ID 列表，可与成员寻址黄页核对）、"
                "scanned_count（成功数）、failed_members（扫描失败的成员 ID）、orphan_count（候选总数）、"
                "orphans（最久的前 20 条，含 mail_id/from/to/subject/age_days）。\n"
                "按需实时扫描全体成员邮箱派生，不存储。\n"
                "典型用途：写社会文摘时列出需要人类处置的孤儿邮件；"
                "未回复只是候选，不能据此认定未送达、失职或故障；成员可以合法沉默。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )
        self._provider = provider

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Return the society's orphan-mail report (read-only)."""
        report = await self._provider()
        return ToolResult(
            success=True,
            data=report,
            message=(
                f"孤儿邮件扫描：扫描成员 {report.get('scanned_count', 0)} 个，"
                f"孤儿邮件 {report.get('orphan_count', 0)} 封"
            ),
        )
