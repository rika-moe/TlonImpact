"""Logging configuration for Tlon."""

import logging
import sys
from typing import Any

import structlog


def _add_trace_context(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Inject trace_id and span_id from ContextVars into log entries."""
    from tlon.base.tracing.trace_context import _current_span_id, _current_trace_id

    trace_id = _current_trace_id.get()
    if trace_id:
        event_dict["trace_id"] = trace_id
    span_id = _current_span_id.get()
    if span_id:
        event_dict["span_id"] = span_id
    return event_dict


def configure_logging(log_level: str = "INFO", debug: bool = False, silent: bool = False) -> None:
    """Configure structured logging for the application.

    Args:
        log_level: The log level (DEBUG, INFO, WARNING, ERROR)
        debug: If True, use more verbose pretty printing
        silent: If True, suppress all log output (used for TUI commands)
    """
    # Configure standard library logging
    if silent:
        logging.basicConfig(
            handlers=[logging.NullHandler()],
            format="%(message)s",
            level=logging.CRITICAL + 1,
            force=True,
        )
    else:
        logging.basicConfig(
            format="%(message)s",
            stream=sys.stdout,
            level=getattr(logging, log_level.upper()),
            force=True,
        )

    # Configure structlog
    shared_processors: list[Any] = [
        _add_trace_context,
        # Add log level to the event
        structlog.stdlib.filter_by_level,
        # Add timestamp
        structlog.processors.TimeStamper(fmt="iso"),
        # Add caller info
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        # Format positional arguments
        structlog.stdlib.PositionalArgumentsFormatter(),
        # Add stack info for exceptions
        structlog.processors.StackInfoRenderer(),
        # Format exception info
        structlog.processors.format_exc_info,
        # Decode unicode
        structlog.processors.UnicodeDecoder(),
    ]

    if debug:
        # Pretty print in debug mode
        processors = shared_processors + [structlog.dev.ConsoleRenderer(colors=True)]
    else:
        # JSON format for production
        processors = shared_processors + [structlog.processors.JSONRenderer(ensure_ascii=False)]

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: The logger name (typically __name__)

    Returns:
        A configured structlog logger
    """
    return structlog.get_logger(name)
