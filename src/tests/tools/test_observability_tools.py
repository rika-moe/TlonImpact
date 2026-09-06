"""Tests for the observability tools (tools/observability/, ).

All four tools are strictly read-only: metrics come from an injected
snapshot callable, traces from the read-only ``TraceStore`` ABC, and the
mail stats from the agents-layer provider over the agent's own mailbox.
"""

from datetime import datetime
from typing import Any

import pytest

from tlon.agents.mail_stats import collect_mail_stats, mail_stats_provider
from tlon.base.paths import TlonPaths
from tlon.base.tracing.trace_store import SpanRecord, TraceRecord, TraceStore
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.tools.observability.get_metrics import GetMetricsTool
from tlon.tools.observability.mail_stats import GetMailStatsTool
from tlon.tools.observability.traces import (
    LIST_TRACES_MAX_LIMIT,
    GetTraceTool,
    ListTracesTool,
)

AGENT_ID = "a-001"
OWN = "a-001@tlon.internal"
BOB = "b-002@tlon.internal"
CAROL = "c-003@tlon.internal"


def _mail(
    mail_id: str,
    from_address: str,
    to: list[str],
    timestamp: str,
    thread_id: str | None = None,
    in_reply_to: str | None = None,
) -> Mail:
    return Mail(
        id=mail_id,
        from_address=from_address,
        to=to,
        cc=[],
        subject=None,
        content="正文",
        in_reply_to=in_reply_to,
        thread_id=thread_id or mail_id,
        forward_of=None,
        timestamp=timestamp,
    )


class TestGetMetricsTool:
    @pytest.mark.asyncio
    async def test_returns_injected_snapshot(self):
        snapshot_data = {
            "metrics": {
                "tlon.llm.calls": {
                    "type": "counter",
                    "description": "",
                    "unit": "",
                    "points": [{"attributes": {"agent_id": AGENT_ID}, "value": 7}],
                }
            }
        }
        tool = GetMetricsTool(snapshot=lambda: snapshot_data)

        result = await tool.execute()

        assert result.success
        assert result.data == snapshot_data

    def test_is_read_only(self):
        tool = GetMetricsTool(snapshot=lambda: {"metrics": {}})
        mutating = [
            name
            for name in dir(tool)
            if name.startswith(("record", "add", "reset", "set_", "write"))
        ]
        assert mutating == []

    def test_tool_schema(self):
        tool = GetMetricsTool(snapshot=lambda: {"metrics": {}})
        assert tool.name == "getMetrics"
        assert tool.parameters["required"] == []


class _FakeTraceStore(TraceStore):
    """In-memory TraceStore recording list_traces calls."""

    def __init__(self, traces: list[TraceRecord]) -> None:
        self._traces = traces
        self.list_calls: list[dict[str, Any]] = []

    async def get_trace(self, trace_id: str) -> TraceRecord | None:
        for trace in self._traces:
            if trace.trace_id == trace_id:
                return trace
        return None

    async def list_traces(
        self, limit: int = 100, before: datetime | None = None, source: str | None = None
    ) -> list[TraceRecord]:
        self.list_calls.append({"limit": limit, "before": before, "source": source})
        traces = [t for t in self._traces if source is None or t.source == source]
        return traces[:limit]


def _trace(trace_id: str, source: str) -> TraceRecord:
    root = SpanRecord(
        span_id=f"{trace_id}-root",
        trace_id=trace_id,
        name="session",
        start_time=datetime.fromisoformat("2026-09-06T10:00:00"),
        end_time=datetime.fromisoformat("2026-09-06T10:01:00"),
        source=source,
    )
    llm_span = SpanRecord(
        span_id=f"{trace_id}-llm",
        trace_id=trace_id,
        name="llm.chat",
        start_time=datetime.fromisoformat("2026-09-06T10:00:10"),
        end_time=datetime.fromisoformat("2026-09-06T10:00:40"),
        source=source,
        parent_span_id=root.span_id,
        payload={
            "llm.model": "moonshot/kimi-k2.5",
            "llm.usage.prompt_tokens": 1234,
            "llm.usage.completion_tokens": 56,
        },
    )
    return TraceRecord(
        trace_id=trace_id,
        root_span_id=root.span_id,
        start_time=root.start_time,
        end_time=root.end_time,
        source=source,
        name="session",
        spans=[root, llm_span],
    )


class TestListTracesTool:
    @pytest.mark.asyncio
    async def test_default_source_is_owner(self):
        store = _FakeTraceStore([_trace("t1", AGENT_ID), _trace("t2", "b-002")])
        tool = ListTracesTool(trace_store=store, agent_id=AGENT_ID)

        result = await tool.execute()

        assert result.success
        assert store.list_calls[-1]["source"] == AGENT_ID
        assert [t["trace_id"] for t in result.data["traces"]] == ["t1"]
        assert result.data["traces"][0]["span_count"] == 2

    @pytest.mark.asyncio
    async def test_explicit_source_passthrough(self):
        store = _FakeTraceStore([_trace("t1", AGENT_ID), _trace("t2", "b-002")])
        tool = ListTracesTool(trace_store=store, agent_id=AGENT_ID)

        result = await tool.execute(source="b-002")

        assert result.success
        assert store.list_calls[-1]["source"] == "b-002"
        assert [t["trace_id"] for t in result.data["traces"]] == ["t2"]

    @pytest.mark.asyncio
    async def test_limit_capped(self):
        store = _FakeTraceStore([])
        tool = ListTracesTool(trace_store=store, agent_id=AGENT_ID)

        await tool.execute(limit=LIST_TRACES_MAX_LIMIT + 500)

        assert store.list_calls[-1]["limit"] == LIST_TRACES_MAX_LIMIT

    def test_tool_schema(self):
        tool = ListTracesTool(trace_store=_FakeTraceStore([]), agent_id=AGENT_ID)
        assert tool.name == "listTraces"
        limit = tool.parameters["properties"]["limit"]
        assert limit["maximum"] == LIST_TRACES_MAX_LIMIT


class TestGetTraceTool:
    @pytest.mark.asyncio
    async def test_returns_span_tree_with_token_attributes(self):
        store = _FakeTraceStore([_trace("t1", AGENT_ID)])
        tool = GetTraceTool(trace_store=store)

        result = await tool.execute(trace_id="t1")

        assert result.success
        tree = result.data["span_tree"]
        assert tree["span_id"] == "t1-root"
        llm_child = tree["children"][0]
        assert llm_child["name"] == "llm.chat"
        assert llm_child["payload"]["llm.usage.prompt_tokens"] == 1234
        assert llm_child["payload"]["llm.usage.completion_tokens"] == 56

    @pytest.mark.asyncio
    async def test_unknown_trace_fails(self):
        tool = GetTraceTool(trace_store=_FakeTraceStore([]))

        result = await tool.execute(trace_id="missing")

        assert not result.success
        assert "missing" in result.error


@pytest.fixture
def tlon_paths(tmp_path) -> TlonPaths:
    return TlonPaths(home=tmp_path)


@pytest.fixture
def mailbox(tlon_paths: TlonPaths) -> Mailbox:
    return Mailbox(tlon_paths.member_mail_dir(AGENT_ID))


async def _seed_mailbox(mailbox: Mailbox) -> None:
    # Thread the agent initiated with Bob; Bob replied 5 minutes later.
    await mailbox.append_sent(
        _mail("m1", OWN, [BOB], "2026-09-06T10:00:00", thread_id="m1")
    )
    await mailbox.append_inbox(
        _mail("m2", BOB, [OWN], "2026-09-06T10:05:00", thread_id="m1", in_reply_to="m1")
    )
    # Bob also initiated a thread of his own.
    await mailbox.append_inbox(_mail("m3", BOB, [OWN], "2026-09-06T11:00:00", thread_id="m3"))
    # Thread the agent initiated with Carol; never answered.
    await mailbox.append_sent(
        _mail("m4", OWN, [CAROL], "2026-09-06T12:00:00", thread_id="m4")
    )
    # Broadcast: not a correspondent.
    await mailbox.append_sent(
        _mail("m5", OWN, ["all@tlon.internal"], "2026-09-06T13:00:00", thread_id="m5")
    )


class TestCollectMailStats:
    @pytest.mark.asyncio
    async def test_correspondent_stats(self, tlon_paths: TlonPaths, mailbox: Mailbox):
        await _seed_mailbox(mailbox)

        stats = await collect_mail_stats(AGENT_ID, tlon_paths=tlon_paths)

        assert stats["agent_id"] == AGENT_ID
        assert stats["total_sent"] == 3
        assert stats["total_received"] == 2

        bob = stats["correspondents"][BOB]
        assert bob["sent"] == 1
        assert bob["received"] == 2
        assert bob["threads_initiated"] == 1
        assert bob["threads_replied"] == 1
        assert bob["avg_first_reply_seconds"] == 300.0

        carol = stats["correspondents"][CAROL]
        assert carol["sent"] == 1
        assert carol["received"] == 0
        assert carol["threads_initiated"] == 1
        assert carol["threads_replied"] == 0
        assert carol["avg_first_reply_seconds"] is None

    @pytest.mark.asyncio
    async def test_never_replied_and_broadcast_excluded(
        self, tlon_paths: TlonPaths, mailbox: Mailbox
    ):
        await _seed_mailbox(mailbox)

        stats = await collect_mail_stats(AGENT_ID, tlon_paths=tlon_paths)

        assert stats["never_replied"] == [CAROL]
        assert stats["never_replied_total"] == 1
        assert "all@tlon.internal" not in stats["correspondents"]

    @pytest.mark.asyncio
    async def test_empty_mailbox(self, tlon_paths: TlonPaths, mailbox: Mailbox):
        stats = await collect_mail_stats(AGENT_ID, tlon_paths=tlon_paths)

        assert stats["total_sent"] == 0
        assert stats["total_received"] == 0
        assert stats["correspondents"] == {}
        assert stats["never_replied"] == []


class TestGetMailStatsTool:
    @pytest.mark.asyncio
    async def test_execute_returns_provider_stats(
        self, tlon_paths: TlonPaths, mailbox: Mailbox
    ):
        await _seed_mailbox(mailbox)
        tool = GetMailStatsTool(
            provider=mail_stats_provider(tlon_paths), agent_id=AGENT_ID
        )

        result = await tool.execute()

        assert result.success
        assert result.data["correspondents"][BOB]["avg_first_reply_seconds"] == 300.0
        assert result.data["never_replied"] == [CAROL]

    @pytest.mark.asyncio
    async def test_tool_binds_owner_id(self):
        seen: list[str] = []

        async def provider(agent_id: str) -> dict[str, Any]:
            seen.append(agent_id)
            return {"agent_id": agent_id, "total_sent": 0, "total_received": 0,
                    "correspondents": {}, "never_replied": []}

        tool = GetMailStatsTool(provider=provider, agent_id=AGENT_ID)
        result = await tool.execute()

        assert result.success
        assert seen == [AGENT_ID]

    def test_tool_schema(self):
        async def provider(agent_id: str) -> dict[str, Any]:
            return {}

        tool = GetMailStatsTool(provider=provider, agent_id=AGENT_ID)
        assert tool.name == "getMailStats"
        assert tool.parameters["required"] == []
