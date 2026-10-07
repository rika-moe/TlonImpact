"""Trace tools — read-only access to the persisted trace store ().

``TraceStore`` (``tlon.base.tracing.trace_store``) is already a read-only
ABC — spans are written by the OpenTelemetry pipeline, never by readers —
so these tools can hold the store directly without a write path to guard.
"""

from typing import Any

from tlon.base.tracing.trace_store import TraceRecord, TraceStore
from tlon.tools.base import Tool, ToolResult

#: Hard cap on listTraces results (agent-facing payload, not the REST cap).
LIST_TRACES_MAX_LIMIT = 100

_LIST_TRACES_DEFAULT_LIMIT = 20


def _trace_summary(trace: TraceRecord) -> dict[str, Any]:
    """Compact per-trace view for listTraces (spans stay in getTrace)."""
    summary: dict[str, Any] = {
        "trace_id": trace.trace_id,
        "name": trace.name,
        "source": trace.source,
        "start_time": trace.start_time.isoformat(),
        "span_count": len(trace.spans),
    }
    if trace.end_time:
        summary["end_time"] = trace.end_time.isoformat()
        summary["duration_ms"] = (trace.end_time - trace.start_time).total_seconds() * 1000
    return summary


class ListTracesTool(Tool):
    """Tool to list recent traces, newest first.

    ``source`` defaults to the owning agent's id (bound at assembly) so
    the common question is "my recent sessions"; passing another member's
    id (or a component source like ``mail_delivery``) looks at the rest
    of the society. Traces persist across restarts (JSONL files), unlike
    the metrics snapshot.
    """

    def __init__(self, trace_store: TraceStore, agent_id: str) -> None:
        """Initialize the tool.

        Args:
            trace_store: Read-only trace storage
            agent_id: Owning agent id — the default ``source`` filter
        """
        super().__init__(
            name="listTraces",
            description=(
                "列出最近的 trace（执行轨迹），按开始时间从新到旧（只读）。\n"
                "- 默认只看你自己（source 默认为你的 agent id）；"
                "传其他成员 id 或组件名（如 'mail_delivery'）可看社会其他部分的轨迹。\n"
                "- 返回摘要列表（trace_id/name/source/起止时间/span 数）；"
                "看某条 trace 的完整 span 树用 getTrace。\n"
                "典型问题：我最近的任务成功率在下降吗？哪类任务耗时变长？"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "返回条数上限（默认 20，最大 100）",
                        "minimum": 1,
                        "maximum": 100,
                    },
                    "source": {
                        "type": "string",
                        "description": (
                            "按来源过滤：agent id（如 'gu-yanxi'）或组件名"
                            "（如 'mail_delivery'）。默认是你自己"
                        ),
                    },
                },
                "required": [],
            },
        )
        self._trace_store = trace_store
        self._agent_id = agent_id

    async def execute(self, **kwargs: Any) -> ToolResult:
        """List traces (read-only), newest first."""
        limit = kwargs.get("limit")
        limit = int(limit) if isinstance(limit, (int, float)) else _LIST_TRACES_DEFAULT_LIMIT
        limit = max(1, min(limit, LIST_TRACES_MAX_LIMIT))
        source = kwargs.get("source") or self._agent_id

        traces = await self._trace_store.list_traces(limit=limit, source=str(source))
        return ToolResult(
            success=True,
            data={
                "traces": [_trace_summary(trace) for trace in traces],
                "count": len(traces),
                "source": str(source),
            },
            message=f"共 {len(traces)} 条 trace（source={source}）",
        )


class GetTraceTool(Tool):
    """Tool to read one full trace with its span tree."""

    def __init__(self, trace_store: TraceStore) -> None:
        """Initialize the tool.

        Args:
            trace_store: Read-only trace storage
        """
        super().__init__(
            name="getTrace",
            description=(
                "按 trace_id 读取一条完整 trace：全部 span 及层级树（只读）。\n"
                "每个 span 的 payload 携带其属性：LLM span 上的 "
                "llm.usage.prompt_tokens / llm.usage.completion_tokens 是 token 消耗，"
                "llm.model 是模型名；工具 span 上有 tool_name 等。"
                "把多条 trace 的 LLM span 属性加起来即可聚合 token 消耗趋势。\n"
                "先用 listTraces 找到感兴趣的 trace_id。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "trace_id": {
                        "type": "string",
                        "description": "trace 标识（从 listTraces 结果获取）",
                    },
                },
                "required": ["trace_id"],
            },
        )
        self._trace_store = trace_store

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Return the full trace record plus its span tree (read-only)."""
        trace_id = str(kwargs["trace_id"])
        trace = await self._trace_store.get_trace(trace_id)
        if trace is None:
            return ToolResult(
                success=False,
                data=None,
                error=f"trace '{trace_id}' 不存在",
            )
        return ToolResult(
            success=True,
            data={"trace": trace.to_dict(), "span_tree": trace.get_span_tree()},
            message=f"trace '{trace_id}'：{len(trace.spans)} 个 span",
        )
