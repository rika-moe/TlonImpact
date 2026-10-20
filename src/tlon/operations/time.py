"""Host clock read, independent of execution devices."""

from datetime import datetime
from typing import Any

from tlon.operations.core import CallContext, Operation


def time_operation_definitions() -> tuple[Operation, ...]:
    async def now(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        value = datetime.now().astimezone()
        return {
            "iso": value.isoformat(timespec="seconds"),
            "unix": int(value.timestamp()),
            "timezone": value.tzname() or "",
            "weekday": value.strftime("%A"),
        }

    return (
        Operation(
            "time-now",
            "Read current host local time with timezone and Unix timestamp.",
            {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            {
                "type": "object",
                "properties": {
                    "iso": {"type": "string"},
                    "unix": {"type": "integer"},
                    "timezone": {"type": "string"},
                    "weekday": {"type": "string"},
                },
                "required": ["iso", "unix", "timezone", "weekday"],
                "additionalProperties": False,
            },
            now,
            effects=frozenset({"read"}),
        ),
    )
