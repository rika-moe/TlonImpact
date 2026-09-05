"""Current time tool — host local time, no sandbox needed."""

from datetime import datetime
from typing import Any

from tlon.tools.base import Tool, ToolResult


class GetCurrentTimeTool(Tool):
    """Tool to get the current time (host local time, ISO 8601 with timezone).

    Reads the server host's clock — the same timezone the human user lives
    in — so it works even when the Docker sandbox is unavailable.

    Example:
        >>> tool = GetCurrentTimeTool()
        >>> result = await tool.execute()
    """

    def __init__(self) -> None:
        """Initialize the tool (no dependencies)."""
        super().__init__(
            name="getCurrentTime",
            description=(
                "获取当前时间（宿主机本地时间，ISO 8601 格式含时区，另附 Unix 时间戳）。"
                "需要知道「现在几点/今天几号/周几」时使用此工具，"
                "不要用 executeBashCommand 跑 date。"
            ),
            parameters={"type": "object", "properties": {}},
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Return the current host local time."""
        now = datetime.now().astimezone()
        iso = now.isoformat(timespec="seconds")
        return ToolResult(
            success=True,
            data={
                "iso": iso,
                "unix": int(now.timestamp()),
                "timezone": now.tzname() or "",
                "weekday": now.strftime("%A"),
            },
            message=f"🕐 {iso}",
        )
