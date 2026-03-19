"""Trace context management for Tlon.

Provides context managers and utilities for working with traces
in a way that automatically propagates trace IDs through the call stack.
"""

import contextvars
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from tlon.base.logger import get_logger

logger = get_logger(__name__)

# Context variables for trace propagation
_current_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_trace_id", default=None
)
_current_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_span_id", default=None
)


@dataclass
class TraceContext:
    """Context for a trace operation.

    Attributes:
        trace_id: The trace identifier
        span_id: The current span identifier
        parent_span_id: The parent span identifier (if any)
    """

    trace_id: str
    span_id: str
    parent_span_id: str | None = None

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary for event creation."""
        result = {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
        }
        if self.parent_span_id:
            result["parent_span_id"] = self.parent_span_id
        return result


def get_current_trace() -> TraceContext | None:
    """Get the current trace context.

    Returns:
        TraceContext if a trace is active, None otherwise
    """
    trace_id = _current_trace_id.get()
    span_id = _current_span_id.get()

    if trace_id and span_id:
        return TraceContext(
            trace_id=trace_id,
            span_id=span_id,
        )
    return None


def generate_trace_id() -> str:
    """Generate a new trace ID.

    Returns:
        UUID v4 string
    """
    return str(uuid.uuid4())


def generate_span_id() -> str:
    """Generate a new span ID.

    Returns:
        Short UUID string (first 16 chars)
    """
    return str(uuid.uuid4())[:16]


@contextmanager
def start_trace(
    trace_id: str | None = None,
    name: str = "unnamed",
    source: str = "system",
    metadata: dict[str, Any] | None = None,
) -> Generator[TraceContext, None, None]:
    """Start a new trace context.

    Args:
        trace_id: Optional trace ID (generated if not provided)
        name: Trace name
        source: Component initiating the trace
        metadata: Optional metadata

    Yields:
        TraceContext for the new trace

    Example:
        >>> with start_trace(name="message_processing", source="agent-001") as ctx:
        ...     # Do work with trace context
        ...     await process_message(ctx, message)
    """
    new_trace_id = trace_id or generate_trace_id()
    root_span_id = generate_span_id()

    # Set context variables
    trace_token = _current_trace_id.set(new_trace_id)
    span_token = _current_span_id.set(root_span_id)

    ctx = TraceContext(
        trace_id=new_trace_id,
        span_id=root_span_id,
    )

    logger.debug(f"Started trace {new_trace_id}: {name}")

    try:
        yield ctx
    finally:
        # Restore previous context
        _current_trace_id.reset(trace_token)
        _current_span_id.reset(span_token)
        logger.debug(f"Ended trace {new_trace_id}")


@contextmanager
def start_span(
    name: str,
    source: str,
    parent_span_id: str | None = None,
    event_type: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Generator[TraceContext, None, None]:
    """Start a new span within the current trace.

    Args:
        name: Span name
        source: Component creating the span
        parent_span_id: Optional parent span ID (defaults to current span)
        event_type: Type of event this span represents
        payload: Additional span data

    Yields:
        TraceContext for the new span

    Example:
        >>> with start_span(name="llm_request", source="agent-001") as ctx:
        ...     response = await llm_client.chat(...)
    """
    current_trace = _current_trace_id.get()
    current_span = _current_span_id.get()

    if not current_trace:
        # No active trace, create a new one
        with start_trace(name=name, source=source) as ctx:
            yield ctx
        return

    new_span_id = generate_span_id()
    parent_id = parent_span_id or current_span

    # Set new span as current
    span_token = _current_span_id.set(new_span_id)

    ctx = TraceContext(
        trace_id=current_trace,
        span_id=new_span_id,
        parent_span_id=parent_id,
    )

    logger.debug(f"Started span {new_span_id} in trace {current_trace}: {name}")

    try:
        yield ctx
    finally:
        # Restore previous span
        _current_span_id.reset(span_token)
        logger.debug(f"Ended span {new_span_id}")


@contextmanager
def continue_trace(
    trace_id: str, parent_span_id: str | None = None, source: str = "system"
) -> Generator[TraceContext, None, None]:
    """Continue an existing trace from another context.

    Args:
        trace_id: The trace ID to continue
        parent_span_id: Optional parent span ID
        source: Component continuing the trace

    Yields:
        TraceContext for the continued trace

    Example:
        >>> # In a message handler receiving a trace_id
        >>> with continue_trace(trace_id, parent_span_id) as ctx:
        ...     await process_with_trace(ctx, message)
    """
    new_span_id = generate_span_id()

    # Set context variables
    trace_token = _current_trace_id.set(trace_id)
    span_token = _current_span_id.set(new_span_id)

    ctx = TraceContext(
        trace_id=trace_id,
        span_id=new_span_id,
        parent_span_id=parent_span_id,
    )

    logger.debug(f"Continued trace {trace_id} with span {new_span_id}")

    try:
        yield ctx
    finally:
        # Restore previous context
        _current_trace_id.reset(trace_token)
        _current_span_id.reset(span_token)
        logger.debug(f"Ended continued span {new_span_id}")
