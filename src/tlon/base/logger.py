"""Logging configuration for Tlon."""

import logging
import sys
from typing import Any

import structlog


def configure_logging(log_level: str = "INFO", debug: bool = False) -> None:
    """Configure structured logging for the application.

    Args:
        log_level: The log level (DEBUG, INFO, WARNING, ERROR)
        debug: If True, use more verbose pretty printing
    """
    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper()),
    )

    # Configure structlog
    shared_processors: list[Any] = [
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
