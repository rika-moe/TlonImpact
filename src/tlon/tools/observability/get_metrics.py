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
                "注意：数据是进程内存态，服务重启即清零，只能回答「本轮进程以来」的问题。"
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
