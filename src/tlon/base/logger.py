"""Logging configuration for Tlon."""

import logging
import sys
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, cast

import structlog


def _add_trace_context(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Inject trace_id and span_id from the OTel context into log entries."""
    from opentelemetry import trace

    span_context = trace.get_current_span().get_span_context()
    if span_context.trace_id:
        event_dict["trace_id"] = format(span_context.trace_id, "032x")
    if span_context.span_id:
        event_dict["span_id"] = format(span_context.span_id, "016x")
    return event_dict


def configure_logging(
    log_level: str = "INFO",
    debug: bool = False,
    silent: bool = False,
    log_dir: Path | None = None,
    retention_days: int = 7,
) -> None:
    """Configure structured logging for the application.

    Rendering is delegated to ``structlog.stdlib.ProcessorFormatter`` on each
    stdlib handler, so stdout and the optional file sink emit the same
    structured records (the file sink is always JSONL, even in debug mode).

    Args:
        log_level: The log level (DEBUG, INFO, WARNING, ERROR)
        debug: If True, use more verbose pretty printing on stdout
        silent: If True, suppress all log output (used for TUI commands);
            no file handler is created even if log_dir is given
        log_dir: Optional directory for a file sink writing
            ``server-{date}.jsonl`` (rotated daily)
        retention_days: Number of daily log files to keep
    """
    # Processors shared by structlog loggers and foreign (plain stdlib) records
    shared_processors: list[Any] = [
        # Merge contextvars (e.g. agent_id) into the event
        structlog.contextvars.merge_contextvars,
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

    def _formatter(renderer: Any) -> structlog.stdlib.ProcessorFormatter:
        return structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        )

    # Configure standard library logging
    if silent:
        handlers: list[logging.Handler] = [logging.NullHandler()]
        level = logging.CRITICAL + 1
    else:
        stdout_renderer: Any = (
            structlog.dev.ConsoleRenderer(colors=True)
            if debug
            else structlog.processors.JSONRenderer(ensure_ascii=False)
        )
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(_formatter(stdout_renderer))
        handlers = [stream_handler]

        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_name = f"server-{datetime.now():%Y-%m-%d}.jsonl"
            file_handler = TimedRotatingFileHandler(
                log_dir / file_name,
                when="midnight",
                backupCount=retention_days,
                encoding="utf-8",
            )
            file_handler.setFormatter(
                _formatter(structlog.processors.JSONRenderer(ensure_ascii=False))
            )
            handlers.append(file_handler)

        level = getattr(logging, log_level.upper())

    logging.basicConfig(handlers=handlers, level=level, force=True)

    # Configure structlog: rendering happens in the per-handler formatters
    structlog.configure(
        processors=shared_processors + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance.

    Args:
        name: The logger name (typically __name__)

    Returns:
        A configured structlog logger
    """
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
