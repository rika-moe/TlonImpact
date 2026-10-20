"""Strict contracts retain public observation and private mailbox scope."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tlon.agents.mail_stats import mail_stats_provider
from tlon.base.paths import TlonPaths
from tlon.base.tracing.trace_store import SpanRecord, TraceRecord, TraceStore
from tlon.operations.adapters import DSLAdapter, OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.observability import make_observability_operations
from tlon.operations.schema import OutputError, json_value
from tlosure.builtins import BuiltinCall
from tlosure.edn import Keyword


def _bound(member: str, **kwargs: Any) -> OperationExecutor:
    return OperationExecutor(
        OperationRegistry(
            make_observability_operations(
                member_id=member,
                mail_stats_provider=kwargs.pop("mail_stats_provider", None),
                reputation_provider=kwargs.pop("reputation_provider", None),
                orphan_provider=kwargs.pop("orphan_provider", None),
                **kwargs,
            )
        ),
        CallContext(member, "run", "call"),
    )


async def test_private_mail_stats_owner_cannot_be_overridden(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    provider = mail_stats_provider(paths)
    alice, bob = (
        _bound("alice", mail_stats_provider=provider),
        _bound("bob", mail_stats_provider=provider),
    )
    a = await OperationTool(alice, "mail-stats").execute()
    b = await OperationTool(bob, "mail-stats").execute()
    assert a.success and b.success
    assert a.data["agent_id"] == "alice" and b.data["agent_id"] == "bob"
    denied = await OperationTool(alice, "mail-stats").execute(member_id="bob")
    assert not denied.success and "InputError" in denied.error
    adapter = DSLAdapter(alice)
    value = await adapter.handle(
        BuiltinCall("mail-stats", adapter.builtins()["mail-stats"], [{}], {})
    )
    assert json_value(value, edn_keys=True) == a.data


async def test_public_cross_member_traces_and_full_schema() -> None:
    store = AsyncMock(spec=TraceStore)
    now = datetime.now(UTC)
    span = SpanRecord("span", "trace", "test", now, "bob", payload={"llm.usage.prompt_tokens": 3})
    record = TraceRecord("trace", "span", now, "bob", "session", spans=[span])
    store.list_traces.return_value = [record]
    store.get_trace.return_value = record
    executor = _bound("alice", trace_store=store)
    adapter = DSLAdapter(executor)
    value = await adapter.handle(
        BuiltinCall(
            "traces-list",
            adapter.builtins()["traces-list"],
            [{Keyword("source"): "bob", Keyword("limit"): 100}],
            {},
        )
    )
    store.list_traces.assert_awaited_once_with(limit=100, source="bob")
    assert json_value(value, edn_keys=True)["source"] == "bob"
    result = await OperationTool(executor, "trace-read").execute(trace_id="trace")
    assert result.success and result.data["span_tree"]["payload"] == {"llm.usage.prompt_tokens": 3}
    store.list_traces.reset_mock()
    bad = await OperationTool(executor, "traces-list").execute(limit=101)
    assert not bad.success
    store.list_traces.assert_not_awaited()


async def test_metrics_non_json_and_missing_known_output_rejected() -> None:
    executor = _bound("alice", metrics_snapshot=lambda: {"metrics": {}, "extra": "bad"})
    with pytest.raises(OutputError):
        await executor.call("metrics-read", {})
    executor = _bound(
        "alice", metrics_snapshot=lambda: {"metrics": {"bad": {"points": [], "value": object()}}}
    )
    result = await OperationTool(executor, "metrics-read").execute()
    assert not result.success and "OutputError" in result.error
    executor = _bound("alice", mail_stats_provider=AsyncMock(return_value={"agent_id": "alice"}))
    result = await OperationTool(executor, "mail-stats").execute()
    assert not result.success and "OutputError" in result.error
