"""Get metrics tool — read-only snapshot of the process-wide metrics ()."""

from typing import Any

from tlon.tools.base import Tool, ToolResult
from tlon.tools.observability.ports import MetricsSnapshot


class GetMetricsTool(Tool):
    """Tool to read the current in-process metrics snapshot.

    Strictly read-only: the tool holds no reference to the metrics
    pipeline, only to the ``snapshot()`` read method. The data is
    process-memory state — counters and histograms reset on restart.
    """

    def __init__(self, snapshot: MetricsSnapshot) -> None:
        """Initialize the tool.

        Args:
            snapshot: Zero-argument callable returning the current
                metrics snapshot dict (``MetricsService.snapshot``)
        """
        super().__init__(
            name="getMetrics",
            description=(
                "读取当前进程的指标快照（只读）。返回全部 counter/histogram/gauge 的当前值，"
                "计数点带 attributes 标签（如 agent_id、tool_name），"
                "histogram 点带 count/sum/min/max。\n"
                "典型问题：我的 LLM 调用次数/时延趋势如何？哪个工具调用最多、错误最多？\n"
                "口径：counter 是本轮进程以来累计事件次数，不是当前成员数；gauge 是最近采集快照。\n"
                "tlon.mail.sent 按封计数，agent 标签是发件方内部键（human:用户名、adam 或成员 ID）；\n"
                "tlon.mail.delivered 按收件人入箱/网关处理成功次数计数，不表示已阅读、回复或 agent 已执行。\n"
                "一封多收件人邮件可投递多次，两项不能直接相减判断丢信；按 mail_id 查明细或 post_office trace。\n"
                "tlon.society.containers 是最近采集到的运行容器数，不是成员数；L0 无容器，其他成员也按需创建。\n"
                "tlon.agent.started 是累计启动次数，同一成员可多次启动，不能与容器数直接对账。\n"
                "注意：指标是进程内存态，服务重启清零；没有记录不等于没有发生。"
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )
        self._snapshot = snapshot

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Return the current metrics snapshot (read-only)."""
        return ToolResult(
            success=True,
            data=self._snapshot(),
            message="当前进程指标快照（内存态，重启清零）",
        )
