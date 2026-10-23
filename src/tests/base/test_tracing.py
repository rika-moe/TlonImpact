"""Tests for the OTel-based tracing infrastructure.

Covers:
a) JsonlSpanExporter output is readable by FileTraceStore.get_trace and
   the span tree nests correctly.
b) The trace_context facade (start_span / continue_trace / get_current_trace)
   links parent/child spans and keeps trace IDs consistent.
c) The end-to-end propagation chain (gateway → router → agent → llm) via
   init_tracing + JSONL files.
d) llm.request and tool.{name} spans and their attributes/status.
"""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import litellm
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from tlon.agents.tool_executor import ToolExecutor
from tlon.base.config import LLMConfig
from tlon.base.llm_client import LLMClient
from tlon.base.tracing import otel
from tlon.base.tracing.jsonl_exporter import JsonlSpanExporter
from tlon.base.tracing.trace_context import continue_trace, get_current_trace, start_span
from tlon.base.tracing.trace_store import FileTraceStore
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry


@pytest.fixture
def trace_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install a fresh TracerProvider exporting JSONL into a temp dir."""
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(JsonlSpanExporter(tmp_path)))
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)
    return tmp_path


async def test_exporter_output_readable_by_file_trace_store(trace_dir: Path) -> None:
    """Exporter JSONL round-trips through FileTraceStore with a nested tree."""
    with (
        start_span(
            "message.received",
            source="client_gateway",
            attributes={"message_id": "m-1", "sender": "human"},
        ) as root_ctx,
        start_span("message.routing", source="message_router") as child_ctx,
    ):
        pass

    assert root_ctx.trace_id is not None
    assert child_ctx.trace_id == root_ctx.trace_id
    assert child_ctx.parent_span_id == root_ctx.span_id

    store = FileTraceStore(trace_dir)
    record = await store.get_trace(root_ctx.trace_id)

    assert record is not None
    assert record.trace_id == root_ctx.trace_id
    assert record.root_span_id == root_ctx.span_id
    assert record.name == "message.received"
    assert record.source == "client_gateway"
    assert record.end_time is not None
    assert len(record.spans) == 2

    # Span attributes landed in payload / event_type
    root_span = next(s for s in record.spans if s.span_id == root_ctx.span_id)
    assert root_span.parent_span_id is None
    assert root_span.payload is not None
    assert root_span.payload["message_id"] == "m-1"
    assert root_span.payload["tlon.source"] == "client_gateway"
    assert root_span.event_type == "message.received"
    assert root_span.end_time is not None

    child_span = next(s for s in record.spans if s.span_id == child_ctx.span_id)
    assert child_span.parent_span_id == root_ctx.span_id

    # Span tree nests correctly
    tree = record.get_span_tree()
    assert tree["span_id"] == root_ctx.span_id
    assert len(tree["children"]) == 1
    assert tree["children"][0]["span_id"] == child_ctx.span_id


def test_facade_start_span_and_continue_trace(trace_dir: Path) -> None:
    """start_span / continue_trace preserve trace ID and parent linkage."""
    assert get_current_trace() is None

    with start_span("message.received", source="client_gateway") as root_ctx:
        current = get_current_trace()
        assert current is not None
        assert current.trace_id == root_ctx.trace_id
        assert current.span_id == root_ctx.span_id

    # Context is restored after the span ends
    assert get_current_trace() is None

    # A remote carrier (e.g. from an event) continues the same trace
    assert root_ctx.trace_id is not None
    with (
        continue_trace(root_ctx.trace_id, root_ctx.span_id),
        start_span("message.routing", source="message_router") as child_ctx,
    ):
        assert child_ctx.trace_id == root_ctx.trace_id
        assert child_ctx.parent_span_id == root_ctx.span_id

    assert get_current_trace() is None


def test_continue_trace_with_invalid_carrier_detaches(trace_dir: Path) -> None:
    """An invalid carrier must not crash; inner spans start a new trace."""
    with continue_trace("not-a-hex-trace-id", "zz"), start_span("orphan", source="test") as ctx:
        assert ctx.trace_id is not None
        assert ctx.parent_span_id is None


# ── End-to-end chain / llm / tool spans ──


@pytest.fixture
def otel_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run init_tracing against a temp dir with full test isolation."""
    monkeypatch.setattr(otel, "_initialized", False)
    provider = otel.init_tracing(tmp_path)
    # Bypass OTel's one-time set_tracer_provider guard (already used above)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)
    yield tmp_path  # type: ignore[misc]
    otel.shutdown_tracing()


@pytest.fixture
def memory_spans(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    """Install a fresh TracerProvider backed by an in-memory exporter."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)
    return exporter


class _FakeUsage:
    def model_dump(self) -> dict[str, int]:
        return {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


class _FakeMessage:
    content = "hello"
    tool_calls = None


class _FakeChoice:
    message = _FakeMessage()
    finish_reason = "stop"


class _FakeResponse:
    choices = [_FakeChoice()]
    usage = _FakeUsage()


def _make_llm_client(
    monkeypatch: pytest.MonkeyPatch, create_return: Any = None, create_side_effect: Any = None
) -> LLMClient:
    """Build an LLMClient whose underlying litellm call is mocked out."""
    config = LLMConfig(
        model_name="openai/test-model",
        max_retries=1,
        base_delay=0.0,
    )
    # The preflight (ADR 0016) requires the provider key env var; litellm
    # itself is mocked, so the value never leaves the test.
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    monkeypatch.setattr(
        litellm,
        "acompletion",
        AsyncMock(return_value=create_return, side_effect=create_side_effect),
    )
    return LLMClient(config)


async def test_end_to_end_propagation_chain(
    otel_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gateway → router → agent session → think → llm.request form one tree."""
    # Gateway: root span for an incoming message
    with start_span("message.received", source="client_gateway") as gw_ctx:
        gw_trace_id, gw_span_id = gw_ctx.trace_id, gw_ctx.span_id
    assert gw_trace_id is not None

    # Router: continues the trace from the event carrier
    with (
        continue_trace(gw_trace_id, gw_span_id),
        start_span("message.routing", source="message_router") as router_ctx,
    ):
        router_span_id = router_ctx.span_id

    # Agent: continues from the router's carrier, then runs a ReAct round
    with (
        continue_trace(gw_trace_id, router_span_id),
        start_span("agent.session", source="agent-1"),
        start_span("agent.think", source="agent-1"),
    ):
        client = _make_llm_client(monkeypatch, create_return=_FakeResponse())
        response = await client.chat(
            system_prompt="sys", messages=[{"role": "user", "content": "hi"}]
        )
    assert response.content == "hello"

    store = FileTraceStore(otel_dir)
    record = await store.get_trace(gw_trace_id)
    assert record is not None

    tree = record.get_span_tree()
    assert tree["name"] == "message.received"

    routing = tree["children"][0]
    assert routing["name"] == "message.routing"

    session = routing["children"][0]
    assert session["name"] == "agent.session"

    think = session["children"][0]
    assert think["name"] == "agent.think"

    llm = think["children"][0]
    assert llm["name"] == "llm.request"
    assert llm["payload"] is not None
    assert llm["payload"]["llm.model"] == "openai/test-model"
    assert llm["payload"]["llm.message_count"] == 2
    assert llm["payload"]["llm.usage.prompt_tokens"] == 10
    assert llm["payload"]["llm.usage.completion_tokens"] == 5


async def test_llm_request_span_success(
    memory_spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """llm.request span records model, message count and usage attributes."""
    client = _make_llm_client(monkeypatch, create_return=_FakeResponse())
    await client.chat(system_prompt="sys", messages=[{"role": "user", "content": "hi"}])

    spans = memory_spans.get_finished_spans()
    llm_span = next(s for s in spans if s.name == "llm.request")
    assert llm_span.status.status_code == StatusCode.UNSET
    attrs = dict(llm_span.attributes or {})
    assert attrs["llm.model"] == "openai/test-model"
    assert attrs["llm.message_count"] == 2
    assert attrs["llm.usage.prompt_tokens"] == 10
    assert attrs["llm.usage.completion_tokens"] == 5


async def test_llm_request_span_failure(
    memory_spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing LLM call sets ERROR status and records the exception."""
    client = _make_llm_client(monkeypatch, create_side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        await client.chat(system_prompt="sys", messages=[{"role": "user", "content": "hi"}])

    spans = memory_spans.get_finished_spans()
    llm_span = next(s for s in spans if s.name == "llm.request")
    assert llm_span.status.status_code == StatusCode.ERROR
    assert any(e.name == "exception" for e in llm_span.events)


def _projection(name: str) -> OperationTool:
    def handler(context: CallContext, arguments: dict[str, Any]) -> Any:
        if name == "boom":
            raise RuntimeError("kaboom")
        return {"echo": arguments["text"]}

    properties = {"text": {"type": "string"}} if name == "echo" else {}
    operation = Operation(
        name,
        "Tracing test",
        {"type": "object", "properties": properties, "required": [], "additionalProperties": False},
        True,
        handler,
    )
    execution = OperationExecutor(OperationRegistry([operation]), CallContext("a", "r", "c"))
    return OperationTool(execution, name)


async def test_tool_span_success(memory_spans: InMemorySpanExporter) -> None:
    """tool.{name} span records name, argument summary and success."""
    executor = ToolExecutor()
    executor.register_tool(_projection("echo"))

    result = await executor.execute("echo", {"text": "hi"})
    assert result.success

    spans = memory_spans.get_finished_spans()
    tool_span = next(s for s in spans if s.name == "tool.echo")
    assert tool_span.status.status_code == StatusCode.UNSET
    attrs = dict(tool_span.attributes or {})
    assert attrs["tool.name"] == "echo"
    assert '"text": "hi"' in attrs["tool.arguments"]
    assert attrs["tool.success"] is True


async def test_tool_span_failure(memory_spans: InMemorySpanExporter) -> None:
    """A failing tool sets ERROR status and tool.error on the span."""
    executor = ToolExecutor()
    executor.register_tool(_projection("boom"))

    with pytest.raises(RuntimeError, match="kaboom"):
        await executor.execute("boom", {})

    spans = memory_spans.get_finished_spans()
    tool_span = next(s for s in spans if s.name == "tool.boom")
    assert tool_span.status.status_code == StatusCode.ERROR
    attrs = dict(tool_span.attributes or {})
    assert attrs["tool.success"] is False
    assert "kaboom" in attrs["tool.error"]
