"""Public society observations and member-private mail statistics."""

from typing import Any

from tlon.base.tracing.trace_store import TraceRecord, TraceStore
from tlon.observability.metrics import _member_snapshot
from tlon.observability.ports import (
    MailStatsProvider,
    MetricsSnapshot,
    OrphanMailProvider,
    ReputationProvider,
)
from tlon.operations.core import CallContext, Operation
from tlon.operations.schema import InputError, OperationError

LIST_TRACES_MAX_LIMIT = 100


def _object(
    properties: dict[str, Any], required: list[str] | None = None, *, dynamic: bool = False
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": dynamic,
    }


def _array(items: Any) -> dict[str, Any]:
    return {"type": "array", "items": items}


_S = {"type": "string"}
_I = {"type": "integer"}
_N = {"type": ["number", "null"]}
_STRINGS = _array(_S)
_DYNAMIC = _object({}, [], dynamic=True)
_SUMMARY = _object(
    {
        "trace_id": _S,
        "name": _S,
        "source": _S,
        "start_time": _S,
        "span_count": _I,
        "end_time": _S,
        "duration_ms": {"type": "number"},
    },
    ["trace_id", "name", "source", "start_time", "span_count"],
)
_SPAN_PROPERTIES = {
    "span_id": _S,
    "trace_id": _S,
    "name": _S,
    "start_time": _S,
    "source": _S,
    "parent_span_id": _S,
    "end_time": _S,
    "duration_ms": {"type": "number"},
    "event_type": _S,
    "payload": _DYNAMIC,
    "metadata": _DYNAMIC,
}
_SPAN = _object(_SPAN_PROPERTIES, ["span_id", "trace_id", "name", "start_time", "source"])
_TRACE = _object(
    {
        "trace_id": _S,
        "root_span_id": _S,
        "name": _S,
        "start_time": _S,
        "source": _S,
        "spans": _array(_SPAN),
        "end_time": _S,
        "duration_ms": {"type": "number"},
        "metadata": _DYNAMIC,
    },
    ["trace_id", "root_span_id", "name", "start_time", "source", "spans"],
)


def _summary(trace: TraceRecord) -> dict[str, Any]:
    result = {
        "trace_id": trace.trace_id,
        "name": trace.name,
        "source": trace.source,
        "start_time": trace.start_time.isoformat(),
        "span_count": len(trace.spans),
    }
    if trace.end_time:
        result.update(
            end_time=trace.end_time.isoformat(),
            duration_ms=(trace.end_time - trace.start_time).total_seconds() * 1000,
        )
    return result


def make_observability_operations(
    *,
    member_id: str,
    mail_stats_provider: MailStatsProvider | None,
    reputation_provider: ReputationProvider | None,
    orphan_provider: OrphanMailProvider | None,
    metrics_snapshot: MetricsSnapshot | None = None,
    trace_store: TraceStore | None = None,
) -> tuple[Operation, ...]:
    """Bind only read ports; public reputation/traces retain cross-member access."""

    async def metrics(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        if metrics_snapshot is None:
            raise OperationError("metrics service is not bound")
        return _member_snapshot(metrics_snapshot())

    async def mail_stats(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        if mail_stats_provider is None:
            raise OperationError("mail statistics service is not bound")
        if context.member_id != member_id:
            raise InputError("mail statistics belong to another member")
        return await mail_stats_provider(member_id)

    async def reputation(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        if reputation_provider is None:
            raise OperationError("reputation service is not bound")
        subject = args.get("member_id") or member_id
        if ":" in subject:
            raise InputError("Expected a public member_id")
        return await reputation_provider(subject)

    async def social(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        if orphan_provider is None:
            raise OperationError("social health service is not bound")
        return await orphan_provider()

    async def traces(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        if trace_store is None:
            raise OperationError("trace service is not bound")
        source = args.get("source") or member_id
        records = await trace_store.list_traces(limit=args.get("limit", 20), source=source)
        return {
            "traces": [_summary(record) for record in records],
            "count": len(records),
            "source": source,
        }

    async def trace(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        if trace_store is None:
            raise OperationError("trace service is not bound")
        record = await trace_store.get_trace(args["trace_id"])
        if record is None:
            raise InputError(f"trace '{args['trace_id']}' 不存在")
        return {"trace": record.to_dict(), "span_tree": record.get_span_tree()}

    return (
        Operation(
            "metrics-read",
            "读取进程指标快照（重启清零）。sent按封、delivered按收件人成功计数，不等于已读或执行；指标标签展示公开成员ID。",
            _object({}),
            _object({"metrics": _DYNAMIC}),
            metrics,
            effects=frozenset({"read"}),
        ),
        Operation(
            "mail-stats",
            "读取自己邮箱的收发、首次回复时长和从未回复对象；不读取他人私有邮箱。",
            _object({}),
            _object(
                {
                    "agent_id": _S,
                    "address": _S,
                    "total_sent": _I,
                    "total_received": _I,
                    "correspondents": _DYNAMIC,
                    "never_replied": _STRINGS,
                    "never_replied_total": _I,
                }
            ),
            mail_stats,
            effects=frozenset({"read"}),
        ),
        Operation(
            "reputation-read",
            "查询公开成员的邮件行为派生声誉，默认自己；致谢按不同成员去重，最多五封高致谢邮件。",
            _object({"member_id": _S}, []),
            _object(
                {
                    "member_id": _S,
                    "address": _S,
                    "endorsements_given": _I,
                    "endorsements_received": _I,
                    "top_endorsed_mails": _array(
                        _object(
                            {
                                "mail_id": _S,
                                "subject": {"type": ["string", "null"]},
                                "endorsers": _I,
                            }
                        )
                    ),
                    "mail_behavior": _object(
                        {
                            "total_sent": _I,
                            "total_received": _I,
                            "reply_rate": _N,
                            "avg_first_reply_seconds": _N,
                        }
                    ),
                }
            ),
            reputation,
            effects=frozenset({"read"}),
        ),
        Operation(
            "social-health",
            "查询社会孤儿邮件报告，返回成功及失败扫描成员与最久的前20封候选；未回复不能据此判定未送达或失职。",
            _object({}),
            _object(
                {
                    "scanned_members": _STRINGS,
                    "scanned_count": _I,
                    "failed_members": _STRINGS,
                    "orphan_count": _I,
                    "orphans": _array(
                        _object(
                            {
                                "mail_id": _S,
                                "from": _S,
                                "to": _STRINGS,
                                "subject": {"type": ["string", "null"]},
                                "age_days": {"type": "number"},
                            }
                        )
                    ),
                }
            ),
            social,
            effects=frozenset({"read"}),
        ),
        Operation(
            "traces-list",
            "列出公开执行轨迹，默认自己；source允许其他成员或组件，按trace起始来源筛选。默认20条，最大100条。",
            _object(
                {
                    "limit": {"type": "integer", "minimum": 1, "maximum": LIST_TRACES_MAX_LIMIT},
                    "source": _S,
                },
                [],
            ),
            _object({"traces": _array(_SUMMARY), "count": _I, "source": _S}),
            traces,
            effects=frozenset({"read"}),
        ),
        Operation(
            "trace-read",
            "按trace_id读取公开完整轨迹及span树；LLM span的usage属性记录token消耗。",
            _object({"trace_id": {"type": "string", "minLength": 1}}),
            _object(
                {
                    "trace": _TRACE,
                    "span_tree": _object({**_SPAN_PROPERTIES, "children": _array(True)}, []),
                }
            ),
            trace,
            effects=frozenset({"read"}),
        ),
    )


def observability_operation_definitions() -> tuple[Operation, ...]:
    """Inspect contracts without opening stores or creating services."""
    return make_observability_operations(
        member_id="", mail_stats_provider=None, reputation_provider=None, orphan_provider=None
    )
