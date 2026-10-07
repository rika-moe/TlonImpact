"""JSONL span exporter for OpenTelemetry.

Appends ended spans to ``{data_dir}/trace-{trace_id}.jsonl`` using the
line format read by FileTraceStore (trace_start / span_start / span_end /
trace_end), so the existing read path (FileTraceStore.get_trace /
list_traces / get_span_tree and the REST /traces endpoints) works unchanged
and old trace files stay readable.

When a root span (no parent) ends, a ``trace_start`` line is written before
its ``span_start`` (once per trace), and a ``trace_end`` line after its
``span_end``.

The exporter interface is synchronous: writes happen directly under a
threading lock.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from tlon.base.logger import get_logger

logger = get_logger(__name__)

SOURCE_ATTRIBUTE = "tlon.source"


def format_trace_id(trace_id: int) -> str:
    """Format an OTel trace ID int as a 32-char hex string (no dashes)."""
    return format(trace_id, "032x")


def format_span_id(span_id: int) -> str:
    """Format an OTel span ID int as a 16-char hex string."""
    return format(span_id, "016x")


def _ns_to_iso(timestamp_ns: int | None) -> str:
    """Convert an OTel nanosecond timestamp to a UTC ISO string."""
    return datetime.fromtimestamp((timestamp_ns or 0) / 1e9, UTC).isoformat()


class JsonlSpanExporter(SpanExporter):
    """SpanExporter that appends span records to per-trace JSONL files.

    Attributes:
        data_dir: Directory for trace files
    """

    def __init__(self, data_dir: Path):
        """Initialize the exporter.

        Args:
            data_dir: Directory to store trace files
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._file_lock = Lock()
        self._started_traces: set[str] = set()

    def _get_trace_file(self, trace_id: str) -> Path:
        """Get the file path for a trace."""
        return self.data_dir / f"trace-{trace_id}.jsonl"

    def _append_lines(self, trace_id: str, events: list[dict[str, Any]]) -> None:
        """Append events to a trace file (called under the file lock)."""
        file_path = self._get_trace_file(trace_id)
        text = "".join(json.dumps(e, ensure_ascii=False, default=str) + "\n" for e in events)
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(text)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Write ended spans to their trace files.

        Args:
            spans: Ended spans to export

        Returns:
            SpanExportResult.SUCCESS on success, FAILURE otherwise
        """
        try:
            with self._file_lock:
                for span in spans:
                    self._write_span(span)
            return SpanExportResult.SUCCESS
        except Exception as e:
            logger.error("span_export_failed", error=str(e), exc_info=True)
            return SpanExportResult.FAILURE

    def _write_span(self, span: ReadableSpan) -> None:
        """Write a single ended span (called under the file lock)."""
        span_context = span.get_span_context()
        if span_context is None:
            return
        trace_id = format_trace_id(span_context.trace_id)
        span_id = format_span_id(span_context.span_id)

        attributes: dict[str, Any] = dict(span.attributes or {})
        source = str(attributes.get(SOURCE_ATTRIBUTE, "unknown"))
        start_ts = _ns_to_iso(span.start_time)
        end_ts = _ns_to_iso(span.end_time)
        is_root = span.parent is None

        events: list[dict[str, Any]] = []

        if is_root and trace_id not in self._started_traces:
            events.append(
                {
                    "type": "trace_start",
                    "trace_id": trace_id,
                    "root_span_id": span_id,
                    "name": span.name,
                    "source": source,
                    "timestamp": start_ts,
                    "metadata": None,
                }
            )
            self._started_traces.add(trace_id)

        events.append(
            {
                "type": "span_start",
                "span_id": span_id,
                "trace_id": trace_id,
                "parent_span_id": format_span_id(span.parent.span_id) if span.parent else None,
                "name": span.name,
                "source": source,
                "event_type": str(attributes.get("event_type", span.name)),
                "timestamp": start_ts,
                "payload": attributes or None,
                "metadata": None,
            }
        )

        events.append(
            {
                "type": "span_end",
                "span_id": span_id,
                "trace_id": trace_id,
                "timestamp": end_ts,
            }
        )

        if is_root:
            events.append(
                {
                    "type": "trace_end",
                    "trace_id": trace_id,
                    "timestamp": end_ts,
                }
            )

        self._append_lines(trace_id, events)

    def shutdown(self) -> None:
        """Shutdown the exporter (no-op; writes are synchronous)."""

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Flush pending spans (no-op; writes are synchronous)."""
        return True
