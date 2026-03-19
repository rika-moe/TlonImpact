"""Trace storage implementation for Tlon.

Provides storage and retrieval of distributed trace data.
"""

import asyncio
import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

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
    parent_span_id: Optional[str] = None
    end_time: Optional[datetime] = None
    event_type: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
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
    def from_dict(cls, data: Dict[str, Any]) -> "SpanRecord":
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
    end_time: Optional[datetime] = None
    spans: List[SpanRecord] = field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
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

    def get_span_tree(self) -> Dict[str, Any]:
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
    """Abstract base class for trace storage.

    Implementations can store traces in files, databases, etc.
    """

    @abstractmethod
    async def start_trace(
        self,
        trace_id: str,
        name: str,
        source: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> TraceRecord:
        """Start a new trace.

        Args:
            trace_id: Unique trace identifier
            name: Trace name
            source: Component initiating the trace
            metadata: Optional metadata

        Returns:
            The created trace record
        """
        pass

    @abstractmethod
    async def end_trace(self, trace_id: str) -> bool:
        """End a trace.

        Args:
            trace_id: Trace identifier

        Returns:
            True if trace was found and ended
        """
        pass

    @abstractmethod
    async def start_span(
        self,
        trace_id: str,
        span_id: str,
        name: str,
        source: str,
        parent_span_id: Optional[str] = None,
        event_type: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> SpanRecord:
        """Start a new span within a trace.

        Args:
            trace_id: Parent trace identifier
            span_id: Unique span identifier
            name: Span name
            source: Component creating the span
            parent_span_id: Parent span ID for nesting
            event_type: Type of event
            payload: Additional data
            metadata: Additional metadata

        Returns:
            The created span record
        """
        pass

    @abstractmethod
    async def end_span(self, trace_id: str, span_id: str) -> bool:
        """End a span.

        Args:
            trace_id: Trace identifier
            span_id: Span identifier

        Returns:
            True if span was found and ended
        """
        pass

    @abstractmethod
    async def get_trace(self, trace_id: str) -> Optional[TraceRecord]:
        """Get a trace by ID.

        Args:
            trace_id: Trace identifier

        Returns:
            Trace record or None if not found
        """
        pass

    @abstractmethod
    async def list_traces(
        self,
        limit: int = 100,
        before: Optional[datetime] = None,
        source: Optional[str] = None
    ) -> List[TraceRecord]:
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
    """File-based trace storage using JSONL format.

    Stores each trace as a separate JSONL file named trace-{trace_id}.jsonl.
    Each line in the file represents an event (span start/end, trace start/end).

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
        self._active_traces: Dict[str, TraceRecord] = {}
        self._active_spans: Dict[str, SpanRecord] = {}
        self._file_lock = Lock()

    def _get_trace_file(self, trace_id: str) -> Path:
        """Get the file path for a trace."""
        return self.data_dir / f"trace-{trace_id}.jsonl"

    def _append_event_sync(self, trace_id: str, event: Dict[str, Any]) -> None:
        """Append an event to a trace file (called in thread pool)."""
        file_path = self._get_trace_file(trace_id)
        line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
        with self._file_lock:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(line)

    async def _append_event(self, trace_id: str, event: Dict[str, Any]) -> None:
        """Append an event to a trace file without blocking the event loop."""
        await asyncio.to_thread(self._append_event_sync, trace_id, event)

    async def start_trace(
        self,
        trace_id: str,
        name: str,
        source: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> TraceRecord:
        """Start a new trace."""
        now = datetime.now()
        # Create a root span for the trace
        root_span_id = str(uuid.uuid4())

        trace = TraceRecord(
            trace_id=trace_id,
            root_span_id=root_span_id,
            start_time=now,
            source=source,
            name=name,
            metadata=metadata,
        )

        self._active_traces[trace_id] = trace

        # Write trace start event
        await self._append_event(trace_id, {
            "type": "trace_start",
            "trace_id": trace_id,
            "root_span_id": root_span_id,
            "name": name,
            "source": source,
            "timestamp": now.isoformat(),
            "metadata": metadata,
        })

        logger.debug(f"Started trace {trace_id}: {name}")
        return trace

    async def end_trace(self, trace_id: str) -> bool:
        """End a trace."""
        trace = self._active_traces.pop(trace_id, None)
        if not trace:
            logger.warning(f"Trace {trace_id} not found for ending")
            return False

        now = datetime.now()
        trace.end_time = now

        # Write trace end event
        await self._append_event(trace_id, {
            "type": "trace_end",
            "trace_id": trace_id,
            "timestamp": now.isoformat(),
        })

        logger.debug(f"Ended trace {trace_id}")
        return True

    async def start_span(
        self,
        trace_id: str,
        span_id: str,
        name: str,
        source: str,
        parent_span_id: Optional[str] = None,
        event_type: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> SpanRecord:
        """Start a new span."""
        now = datetime.now()

        span = SpanRecord(
            span_id=span_id,
            trace_id=trace_id,
            name=name,
            start_time=now,
            source=source,
            parent_span_id=parent_span_id,
            event_type=event_type,
            payload=payload,
            metadata=metadata,
        )

        self._active_spans[span_id] = span

        # Add to trace if we have it in memory
        trace = self._active_traces.get(trace_id)
        if trace:
            trace.spans.append(span)

        # Write span start event
        await self._append_event(trace_id, {
            "type": "span_start",
            "span_id": span_id,
            "trace_id": trace_id,
            "parent_span_id": parent_span_id,
            "name": name,
            "source": source,
            "event_type": event_type,
            "timestamp": now.isoformat(),
            "payload": payload,
            "metadata": metadata,
        })

        logger.debug(f"Started span {span_id} in trace {trace_id}: {name}")
        return span

    async def end_span(self, trace_id: str, span_id: str) -> bool:
        """End a span."""
        span = self._active_spans.pop(span_id, None)
        if not span:
            logger.warning(f"Span {span_id} not found for ending")
            return False

        now = datetime.now()
        span.end_time = now

        # Write span end event
        await self._append_event(trace_id, {
            "type": "span_end",
            "span_id": span_id,
            "trace_id": trace_id,
            "timestamp": now.isoformat(),
        })

        logger.debug(f"Ended span {span_id} in trace {trace_id}")
        return True

    def _load_trace_from_file(self, file_path: Path) -> Optional[TraceRecord]:
        """Load a trace from a JSONL file (runs in thread pool)."""
        trace = None
        spans: Dict[str, SpanRecord] = {}

        with self._file_lock:
            with open(file_path, "r", encoding="utf-8") as f:
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
                        logger.warning(f"Error parsing trace event: {e}")
                        continue

        if trace:
            trace.spans = list(spans.values())
        return trace

    async def get_trace(self, trace_id: str) -> Optional[TraceRecord]:
        """Get a trace by loading from file."""
        # Check if it's an active trace
        if trace_id in self._active_traces:
            return self._active_traces[trace_id]

        # Load from file without blocking the event loop
        file_path = self._get_trace_file(trace_id)
        if not file_path.exists():
            return None
        return await asyncio.to_thread(self._load_trace_from_file, file_path)

    async def list_traces(
        self,
        limit: int = 100,
        before: Optional[datetime] = None,
        source: Optional[str] = None
    ) -> List[TraceRecord]:
        """List traces from files."""
        traces = []

        # Include active traces
        for trace in self._active_traces.values():
            if before and trace.start_time >= before:
                continue
            if source and trace.source != source:
                continue
            traces.append(trace)

        # Load from files
        for file_path in self.data_dir.glob("trace-*.jsonl"):
            # Extract trace_id from filename
            trace_id = file_path.stem.replace("trace-", "")

            # Skip if already in active traces
            if trace_id in self._active_traces:
                continue

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
