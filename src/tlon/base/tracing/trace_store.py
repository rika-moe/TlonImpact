"""Trace storage implementation for Tlon.

Read-only query interface over the per-trace JSONL files written by the
OpenTelemetry JsonlSpanExporter (see ``otel.py`` / ``jsonl_exporter.py``).
"""

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from tlon.base.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SpanRecord:
    """A span within a trace representing a single operation.

    Attributes:
        span_id: Unique span identifier
        trace_id: Parent trace identifier
        parent_span_id: Parent span identifier (for nested spans)
        name: Span name (operation name)
        start_time: When the span started
        end_time: When the span ended (None if still open)
        source: Component that created this span
        event_type: Type of event this span represents
        payload: Additional span data
        metadata: Additional metadata
    """

    span_id: str
    trace_id: str
    name: str
    start_time: datetime
    source: str
    parent_span_id: str | None = None
    end_time: datetime | None = None
    event_type: str | None = None
    payload: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert span to dictionary."""
        result = {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "name": self.name,
            "start_time": self.start_time.isoformat(),
            "source": self.source,
        }
        if self.parent_span_id:
            result["parent_span_id"] = self.parent_span_id
        if self.end_time:
            result["end_time"] = self.end_time.isoformat()
            result["duration_ms"] = (self.end_time - self.start_time).total_seconds() * 1000
        if self.event_type:
            result["event_type"] = self.event_type
        if self.payload:
            result["payload"] = self.payload
        if self.metadata:
            result["metadata"] = self.metadata
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SpanRecord":
        """Create span from dictionary."""
        return cls(
            span_id=data["span_id"],
            trace_id=data["trace_id"],
            name=data["name"],
            start_time=datetime.fromisoformat(data["start_time"]),
            source=data["source"],
            parent_span_id=data.get("parent_span_id"),
            end_time=datetime.fromisoformat(data["end_time"]) if data.get("end_time") else None,
            event_type=data.get("event_type"),
            payload=data.get("payload"),
            metadata=data.get("metadata"),
        )


@dataclass
class TraceRecord:
    """A complete trace record containing all spans.

    Attributes:
        trace_id: Unique trace identifier
        root_span_id: ID of the root span
        start_time: When the trace started
        end_time: When the trace ended
        source: Component that initiated the trace
        name: Trace name/description
        spans: List of spans in this trace
        metadata: Additional trace metadata
    """

    trace_id: str
    root_span_id: str
    start_time: datetime
    source: str
    name: str
    end_time: datetime | None = None
    spans: list[SpanRecord] = field(default_factory=list)
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert trace to dictionary."""
        result = {
            "trace_id": self.trace_id,
            "root_span_id": self.root_span_id,
            "name": self.name,
            "start_time": self.start_time.isoformat(),
            "source": self.source,
            "spans": [span.to_dict() for span in self.spans],
        }
        if self.end_time:
            result["end_time"] = self.end_time.isoformat()
            result["duration_ms"] = (self.end_time - self.start_time).total_seconds() * 1000
        if self.metadata:
            result["metadata"] = self.metadata
        return result

    def get_span_tree(self) -> dict[str, Any]:
        """Get spans as a hierarchical tree structure.

        Returns:
            Tree structure with nested children
        """
        # Build lookup
        span_map = {span.span_id: span.to_dict() for span in self.spans}
        for span_dict in span_map.values():
            span_dict["children"] = []

        # Build tree
        root = None
        for span in self.spans:
            span_dict = span_map[span.span_id]
            if span.parent_span_id and span.parent_span_id in span_map:
                span_map[span.parent_span_id]["children"].append(span_dict)
            elif span.span_id == self.root_span_id:
                root = span_dict

        return root or span_map.get(self.root_span_id, {})


class TraceStore(ABC):
    """Abstract base class for trace storage (read-only).

    Spans are written by the OpenTelemetry pipeline; implementations only
    need to load and list persisted traces.
    """

    @abstractmethod
    async def get_trace(self, trace_id: str) -> TraceRecord | None:
        """Get a trace by ID.

        Args:
            trace_id: Trace identifier

        Returns:
            Trace record or None if not found
        """
        pass

    @abstractmethod
    async def list_traces(
        self, limit: int = 100, before: datetime | None = None, source: str | None = None
    ) -> list[TraceRecord]:
        """List traces with optional filtering.

        Args:
            limit: Maximum number of traces
            before: Only return traces before this time
            source: Filter by source

        Returns:
            List of trace records
        """
        pass


class FileTraceStore(TraceStore):
    """Read-only file-based trace storage using JSONL format.

    Each trace lives in a file named trace-{trace_id}.jsonl, written by the
    OTel JsonlSpanExporter; this class only loads and lists them.

    Attributes:
        data_dir: Directory for trace files
    """

    def __init__(self, data_dir: Path):
        """Initialize file trace store.

        Args:
            data_dir: Directory to store trace files
        """
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _get_trace_file(self, trace_id: str) -> Path:
        """Get the file path for a trace."""
        return self.data_dir / f"trace-{trace_id}.jsonl"

    def _load_trace_from_file(self, file_path: Path) -> TraceRecord | None:
        """Load a trace from a JSONL file (runs in thread pool)."""
        trace = None
        spans: dict[str, SpanRecord] = {}

        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    event_type = event.get("type")

                    if event_type == "trace_start":
                        trace = TraceRecord(
                            trace_id=event["trace_id"],
                            root_span_id=event["root_span_id"],
                            start_time=datetime.fromisoformat(event["timestamp"]),
                            source=event["source"],
                            name=event["name"],
                            metadata=event.get("metadata"),
                        )
                    elif event_type == "trace_end":
                        if trace:
                            trace.end_time = datetime.fromisoformat(event["timestamp"])
                    elif event_type == "span_start":
                        span = SpanRecord(
                            span_id=event["span_id"],
                            trace_id=event["trace_id"],
                            name=event["name"],
                            start_time=datetime.fromisoformat(event["timestamp"]),
                            source=event["source"],
                            parent_span_id=event.get("parent_span_id"),
                            event_type=event.get("event_type"),
                            payload=event.get("payload"),
                            metadata=event.get("metadata"),
                        )
                        spans[span.span_id] = span
                    elif event_type == "span_end":
                        span = spans.get(event["span_id"])
                        if span:
                            span.end_time = datetime.fromisoformat(event["timestamp"])
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning("trace_event_parse_failed", error=str(e))
                    continue

        if trace:
            trace.spans = list(spans.values())
        return trace

    async def get_trace(self, trace_id: str) -> TraceRecord | None:
        """Get a trace by loading from file."""
        file_path = self._get_trace_file(trace_id)
        if not file_path.exists():
            return None
        return await asyncio.to_thread(self._load_trace_from_file, file_path)

    async def list_traces(
        self, limit: int = 100, before: datetime | None = None, source: str | None = None
    ) -> list[TraceRecord]:
        """List traces from files."""
        traces = []

        # Load from files
        for file_path in self.data_dir.glob("trace-*.jsonl"):
            # Extract trace_id from filename
            trace_id = file_path.stem.replace("trace-", "")

            trace = await self.get_trace(trace_id)
            if trace:
                if before and trace.start_time >= before:
                    continue
                if source and trace.source != source:
                    continue
                traces.append(trace)

        # Sort by start time (newest first) and limit
        traces.sort(key=lambda t: t.start_time, reverse=True)
        return traces[:limit]
