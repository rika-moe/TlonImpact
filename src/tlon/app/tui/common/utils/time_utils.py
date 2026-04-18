"""Time formatting utilities for Tlon TUI."""

from datetime import datetime


def format_timestamp(
    timestamp: float | str | datetime | None,
    format_str: str = "%H:%M:%S",
) -> str:
    """Format timestamp for display.

    Args:
        timestamp: Unix timestamp (float), ISO string, or datetime object
        format_str: Output format string

    Returns:
        Formatted time string
    """
    if timestamp is None:
        return "--:--:--"

    try:
        if isinstance(timestamp, datetime):
            dt = timestamp
        elif isinstance(timestamp, str):
            # Try parsing as float first
            try:
                ts = float(timestamp)
                dt = datetime.fromtimestamp(ts)
            except ValueError:
                # Try ISO format
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        else:
            dt = datetime.fromtimestamp(float(timestamp))

        return dt.strftime(format_str)
    except (ValueError, TypeError, OSError):
        return "--:--:--"


def format_duration(seconds: float | int | None) -> str:
    """Format duration for display.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted duration string (e.g., "2d 4h 32m" or "15.2s")
    """
    if seconds is None or seconds < 0:
        return "--"

    total_seconds = float(seconds)

    if total_seconds < 1:
        return f"{total_seconds * 1000:.0f}ms"

    if total_seconds < 60:
        return f"{total_seconds:.1f}s"

    minutes = int(total_seconds // 60)
    remaining_seconds = total_seconds % 60

    if minutes < 60:
        if remaining_seconds > 0:
            return f"{minutes}m {remaining_seconds:.0f}s"
        return f"{minutes}m"

    hours = minutes // 60
    remaining_minutes = minutes % 60

    if hours < 24:
        if remaining_minutes > 0:
            return f"{hours}h {remaining_minutes}m"
        return f"{hours}h"

    days = hours // 24
    remaining_hours = hours % 24

    parts = [f"{days}d"]
    if remaining_hours > 0:
        parts.append(f"{remaining_hours}h")
    if remaining_minutes > 0 and days < 7:
        parts.append(f"{remaining_minutes}m")

    return " ".join(parts)


def format_relative_time(timestamp: float | str | datetime) -> str:
    """Format timestamp as relative time (e.g., "2 minutes ago").

    Args:
        timestamp: Unix timestamp or datetime object

    Returns:
        Relative time string
    """
    try:
        if isinstance(timestamp, datetime):
            dt = timestamp
        elif isinstance(timestamp, str):
            dt = datetime.fromtimestamp(float(timestamp))
        else:
            dt = datetime.fromtimestamp(float(timestamp))

        now = datetime.now()
        diff = now - dt

        seconds = diff.total_seconds()

        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            return f"{minutes}m ago"
        elif seconds < 86400:
            hours = int(seconds // 3600)
            return f"{hours}h ago"
        elif seconds < 604800:
            days = int(seconds // 86400)
            return f"{days}d ago"
        else:
            return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return "unknown"


def parse_time_range(range_str: str) -> tuple[float, float]:
    """Parse time range string to timestamp tuple.

    Args:
        range_str: Time range string (e.g., "1h", "24h", "7d")

    Returns:
        Tuple of (start_timestamp, end_timestamp)
    """
    now = datetime.now().timestamp()

    if range_str.endswith("m"):
        minutes = int(range_str[:-1])
        return (now - minutes * 60, now)
    elif range_str.endswith("h"):
        hours = int(range_str[:-1])
        return (now - hours * 3600, now)
    elif range_str.endswith("d"):
        days = int(range_str[:-1])
        return (now - days * 86400, now)
    else:
        # Default to last hour
        return (now - 3600, now)
