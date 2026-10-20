"""Tool protocol result envelope."""

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    """Result of a tool execution."""

    success: bool
    data: Any
    error: str = ""
    message: str = ""  # 用户友好的消息，用于显示

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolResult":
        """Parse the to_dict shape back into a ToolResult."""
        return cls(
            success=bool(data["success"]),
            data=data.get("data"),
            error=data.get("error", ""),
            message=data.get("message", ""),
        )
