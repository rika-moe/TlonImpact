"""Trace context facade over OpenTelemetry.

Thin wrappers around the OTel tracer/context APIs:

- ``start_span``: start a span as current (``tlon.source`` attribute set),
  yielding a lightweight string-based context handle.
- ``continue_trace``: attach a remote parent context built from a carrier
  (the ``trace_id`` / ``span_id`` fields carried on events), so spans
  started inside join the same trace tree.
- ``get_current_trace``: read the current span context, or None.

Trace/span IDs are formatted as hex strings (32 / 16 chars, no dashes),
matching the format used by JsonlSpanExporter for file names and records.
"""

import random
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from opentelemetry import context, trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from tlon.base.logger import get_logger
from tlon.base.tracing.jsonl_exporter import SOURCE_ATTRIBUTE, format_span_id, format_trace_id

logger = get_logger(__name__)

TRACER_NAME = "tlon"


@dataclass
class SpanCtx:
    """Lightweight handle for an active span context.

    Attributes:
        trace_id: Trace identifier (32-char hex), None if invalid
        span_id: Current span identifier (16-char hex), None if invalid
        parent_span_id: Parent span identifier (if any)
    """

    trace_id: str | None
    span_id: str | None
    parent_span_id: str | None = None


def _get_tracer() -> trace.Tracer:
    """Get the Tlon tracer from the global provider."""
    return trace.get_tracer(TRACER_NAME)


def _ctx_from_span(span: trace.Span) -> SpanCtx:
    """Build a SpanCtx from a started span (recording or not)."""
    sc = span.get_span_context()
    parent = getattr(span, "parent", None)
    return SpanCtx(
        trace_id=format_trace_id(sc.trace_id) if sc.trace_id else None,
        span_id=format_span_id(sc.span_id) if sc.span_id else None,
        parent_span_id=format_span_id(parent.span_id) if parent and parent.span_id else None,
    )


@contextmanager
def start_span(
    name: str,
    source: str,
    attributes: dict[str, Any] | None = None,
) -> Generator[SpanCtx, None, None]:
    """Start a new span as the current span.

    Args:
        name: Span name (e.g. "message.received", "agent.think")
        source: Component creating the span (recorded as ``tlon.source``)
        attributes: Additional span attributes

    Yields:
        SpanCtx for the new span

    Example:
        >>> with start_span("message.routing", source="message_router") as ctx:
        ...     await route(ctx, message)
    """
    attrs: dict[str, Any] = {SOURCE_ATTRIBUTE: source}
    if attributes:
        attrs.update(attributes)

    with _get_tracer().start_as_current_span(name, attributes=attrs) as span:
        yield _ctx_from_span(span)


@contextmanager
def continue_trace(
    trace_id: str, parent_span_id: str | None = None
) -> Generator[SpanCtx, None, None]:
    """Continue an existing trace from a carrier (e.g. event fields).

    Builds a remote parent SpanContext and attaches it, so spans started
    inside this block become children of the remote span and share its
    trace ID.

    Args:
        trace_id: Trace ID from the carrier (32-char hex)
        parent_span_id: Parent span ID from the carrier (16-char hex);
            a random stand-in is used when missing

    Yields:
        SpanCtx describing the attached remote parent

    Example:
        >>> with continue_trace(event.trace_id, event.span_id):
        ...     with start_span("message.routing", source="router") as ctx:
        ...         await route(ctx, message)
    """
    try:
        remote_trace_id = int(trace_id, 16)
        remote_span_id = int(parent_span_id, 16) if parent_span_id else 0
        if remote_trace_id <= 0:
            raise ValueError("trace_id must be positive")
    except ValueError:
        logger.warning(f"Invalid trace carrier (trace_id={trace_id!r}), starting detached")
        yield SpanCtx(trace_id=None, span_id=None)
        return

    if remote_span_id == 0:
        remote_span_id = random.getrandbits(64)

    span_context = SpanContext(
        trace_id=remote_trace_id,
        span_id=remote_span_id,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    otel_ctx = trace.set_span_in_context(NonRecordingSpan(span_context))
    token = context.attach(otel_ctx)
    try:
        yield SpanCtx(
            trace_id=trace_id,
            span_id=parent_span_id,
            parent_span_id=None,
        )
    finally:
        context.detach(token)


def get_current_trace() -> SpanCtx | None:
    """Get the current trace context from the active span.

    Returns:
        SpanCtx if a valid span is active, None otherwise
    """
    sc = trace.get_current_span().get_span_context()
    if not sc.trace_id or not sc.span_id:
        return None
    return SpanCtx(
        trace_id=format_trace_id(sc.trace_id),
        span_id=format_span_id(sc.span_id),
    )
