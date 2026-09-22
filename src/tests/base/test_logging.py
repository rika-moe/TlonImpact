"""Tests for structured logging: file sink, contextvars injection, log endpoints.

Covers:
a) configure_logging(log_dir=...) writes valid JSONL to server-{date}.jsonl,
   with trace_id/span_id injected inside a span.
b) structlog contextvars (agent_id) are merged into log entries and reset
   after bound_contextvars exits.
c) /agents/{id}/logs and /logs REST endpoints filter and paginate correctly.
"""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import structlog
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from structlog.contextvars import bound_contextvars

from tlon.api.members import MembersFacade
from tlon.api.observability import ObservabilityFacade
from tlon.base.logger import configure_logging, get_logger
from tlon.base.tracing.trace_context import start_span
from tlon_server.api.rest import RESTAPIHandler


@pytest.fixture(autouse=True)
def _reset_logging_state():
    """configure_logging mutates global state; restore neutral defaults."""
    yield
    logging.basicConfig(handlers=[logging.NullHandler()], force=True)
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()


@pytest.fixture
def tracer_provider(monkeypatch: pytest.MonkeyPatch) -> TracerProvider:
    """Install a fresh TracerProvider so spans produce real trace/span IDs."""
    provider = TracerProvider()
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)
    return provider


def _read_jsonl(log_dir: Path) -> list[dict[str, Any]]:
    files = list(log_dir.glob("server-*.jsonl"))
    assert len(files) == 1, f"expected one server-*.jsonl, got {files}"
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


# ===== a) File sink =====


def test_file_sink_writes_jsonl(tmp_path: Path) -> None:
    configure_logging(log_level="INFO", log_dir=tmp_path)
    get_logger("test.file_sink").info("hello file", foo="bar")

    entries = _read_jsonl(tmp_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event"] == "hello file"
    assert entry["foo"] == "bar"
    assert entry["level"] == "info"
    assert "timestamp" in entry


def test_file_sink_file_name_contains_today(tmp_path: Path) -> None:
    configure_logging(log_level="INFO", log_dir=tmp_path)
    get_logger("test.file_name").info("x")

    expected = f"server-{date.today():%Y-%m-%d}.jsonl"
    assert (tmp_path / expected).exists()


def test_file_sink_includes_trace_context(tmp_path: Path, tracer_provider: TracerProvider) -> None:
    configure_logging(log_level="INFO", log_dir=tmp_path)
    with start_span("test.span", source="test") as ctx:
        get_logger("test.trace").info("inside span")

    entries = _read_jsonl(tmp_path)
    assert entries[0]["trace_id"] == ctx.trace_id
    assert entries[0]["span_id"] == ctx.span_id


def test_silent_creates_no_file(tmp_path: Path) -> None:
    configure_logging(silent=True, log_dir=tmp_path)
    get_logger("test.silent").info("must not be written")

    assert list(tmp_path.glob("server-*.jsonl")) == []


# ===== b) agent_id contextvars injection =====


def test_agent_id_contextvars(tmp_path: Path) -> None:
    configure_logging(log_level="INFO", log_dir=tmp_path)
    log = get_logger("test.ctx")

    log.info("before")
    with bound_contextvars(agent_id="dev-001"):
        log.info("inside")
    log.info("after")

    entries = _read_jsonl(tmp_path)
    assert "agent_id" not in entries[0]
    assert entries[1]["agent_id"] == "dev-001"
    assert "agent_id" not in entries[2]


# ===== b2) Content fields: full text, no truncation =====


def test_content_field_full_text_not_truncated(tmp_path: Path) -> None:
    """A long message must land in JSONL verbatim (no [:50]-style truncation)."""
    configure_logging(log_level="INFO", log_dir=tmp_path)
    long_content = "很长的用户消息" * 500  # ~4000 chars, well past any [:50] slice

    get_logger("test.content").info("message_received", message_id="m1", content=long_content)

    entries = _read_jsonl(tmp_path)
    assert entries[0]["content"] == long_content


def test_messages_field_keeps_native_list_structure(tmp_path: Path) -> None:
    """LLM request messages stay a native list of dicts in the JSON output."""
    configure_logging(log_level="INFO", log_dir=tmp_path)
    messages = [
        {"role": "system", "content": "你是一个乐于助人的助手。"},
        {"role": "user", "content": "你好"},
    ]

    get_logger("test.llm").info("llm_request", model="kimi-k2.5", message_count=2, messages=messages)

    entries = _read_jsonl(tmp_path)
    assert entries[0]["messages"] == messages
    assert entries[0]["message_count"] == 2


def test_result_field_non_ascii_preserved(tmp_path: Path) -> None:
    """Tool results with non-ASCII content round-trip unescaped (ensure_ascii=False)."""
    configure_logging(log_level="INFO", log_dir=tmp_path)
    result = {"success": True, "data": {"content": "回复：你好，世界 🌍"}}

    get_logger("test.tool").info("tool_result", tool_name="sendEmail", success=True, result=result)

    entries = _read_jsonl(tmp_path)
    assert entries[0]["result"] == result
    raw = list(tmp_path.glob("server-*.jsonl"))[0].read_text(encoding="utf-8")
    assert "你好，世界 🌍" in raw  # not \\uXXXX-escaped


# ===== c) Log endpoints =====


def _write_fake_logs(log_dir: Path, entries: list[dict[str, Any]]) -> None:
    file = log_dir / f"server-{date.today():%Y-%m-%d}.jsonl"
    with file.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _fake_entries() -> list[dict[str, Any]]:
    return [
        {
            "event": "a1",
            "level": "info",
            "timestamp": "2026-07-21T10:00:01",
            "agent_id": "dev-001",
            "trace_id": "t1",
        },
        {
            "event": "b1",
            "level": "error",
            "timestamp": "2026-07-21T10:00:02",
            "agent_id": "dev-002",
            "trace_id": "t1",
        },
        {
            "event": "a2",
            "level": "debug",
            "timestamp": "2026-07-21T10:00:03",
            "agent_id": "dev-001",
            "trace_id": "t2",
        },
        {
            "event": "a3",
            "level": "warning",
            "timestamp": "2026-07-21T10:00:04",
            "agent_id": "dev-001",
        },
    ]


class _FakeAgentConfig:
    name = "测试Agent"


class _FakeAgentRegistry:
    def get_config(self, agent_id: str) -> _FakeAgentConfig | None:
        return _FakeAgentConfig() if agent_id == "dev-001" else None


class _FakeRequest:
    """Minimal stand-in for aiohttp.web.Request for handler unit tests."""

    def __init__(self, match_info: dict[str, str] | None = None, query: dict[str, str] | None = None):
        self.match_info = match_info or {}
        self.query = query or {}


def _make_handler(log_dir: Path) -> RESTAPIHandler:
    return RESTAPIHandler(
        client_gateway=None,
        members=MembersFacade(
            agent_registry=_FakeAgentRegistry(),  # type: ignore[arg-type]
            tlon_paths=MagicMock(),
        ),
        observability=ObservabilityFacade(
            trace_store=MagicMock(), metrics_service=MagicMock(), logs_dir=log_dir
        ),
    )


async def _response_json(response: Any) -> dict[str, Any]:
    return json.loads(response.text)


@pytest.fixture
def handler(tmp_path: Path) -> RESTAPIHandler:
    _write_fake_logs(tmp_path, _fake_entries())
    return _make_handler(tmp_path)


async def test_agent_logs_filters_by_agent_id(handler: RESTAPIHandler) -> None:
    resp = await handler.handle_get_agent_logs(_FakeRequest(match_info={"agent_id": "dev-001"}))
    data = await _response_json(resp)

    assert resp.status == 200
    assert data["agent_id"] == "dev-001"
    assert data["count"] == 3
    assert {e["event"] for e in data["logs"]} == {"a1", "a2", "a3"}


async def test_agent_logs_unknown_agent_404(handler: RESTAPIHandler) -> None:
    resp = await handler.handle_get_agent_logs(_FakeRequest(match_info={"agent_id": "nope"}))
    assert resp.status == 404


async def test_agent_logs_limit(handler: RESTAPIHandler) -> None:
    resp = await handler.handle_get_agent_logs(
        _FakeRequest(match_info={"agent_id": "dev-001"}, query={"limit": "2"})
    )
    data = await _response_json(resp)

    # Tail of the chronological list
    assert data["count"] == 2
    assert [e["event"] for e in data["logs"]] == ["a2", "a3"]


async def test_agent_logs_before(handler: RESTAPIHandler) -> None:
    resp = await handler.handle_get_agent_logs(
        _FakeRequest(
            match_info={"agent_id": "dev-001"},
            query={"before": "2026-07-21T10:00:03"},
        )
    )
    data = await _response_json(resp)

    assert [e["event"] for e in data["logs"]] == ["a1"]


async def test_logs_filter_by_trace_id(handler: RESTAPIHandler) -> None:
    resp = await handler.handle_list_logs(_FakeRequest(query={"trace_id": "t1"}))
    data = await _response_json(resp)

    assert data["count"] == 2
    assert {e["event"] for e in data["logs"]} == {"a1", "b1"}


async def test_logs_filter_by_min_level(handler: RESTAPIHandler) -> None:
    resp = await handler.handle_list_logs(_FakeRequest(query={"level": "warning"}))
    data = await _response_json(resp)

    assert {e["event"] for e in data["logs"]} == {"b1", "a3"}


async def test_logs_combined_filters(handler: RESTAPIHandler) -> None:
    resp = await handler.handle_list_logs(
        _FakeRequest(query={"agent_id": "dev-001", "trace_id": "t2", "level": "debug"})
    )
    data = await _response_json(resp)

    assert [e["event"] for e in data["logs"]] == ["a2"]


async def test_logs_limit_and_before(handler: RESTAPIHandler) -> None:
    resp = await handler.handle_list_logs(
        _FakeRequest(query={"before": "2026-07-21T10:00:04", "limit": "2"})
    )
    data = await _response_json(resp)

    assert [e["event"] for e in data["logs"]] == ["b1", "a2"]


async def test_logs_unavailable_503(tmp_path: Path) -> None:
    handler = RESTAPIHandler(
        client_gateway=None,
        members=MembersFacade(
            agent_registry=_FakeAgentRegistry(),  # type: ignore[arg-type]
            tlon_paths=MagicMock(),
        ),
    )
    resp = await handler.handle_list_logs(_FakeRequest())
    assert resp.status == 503
