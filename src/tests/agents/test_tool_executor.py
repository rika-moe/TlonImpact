"""ToolExecutor single-point fallback contract (issue #3).

Tools no longer swallow their own exceptions: an unexpected failure inside
``Tool.execute`` propagates to ToolExecutor, the single fallback that logs
the error with a stack trace and converts it into
``ToolResult(success=False, error="Execution error: ...")`` — the executor
itself never raises.
"""

from typing import Any

from tlon.agents.tool_executor import ToolExecutor
from tlon.tools.base import Tool, ToolResult


class _ExplodingTool(Tool):
    """Raises from execute, like a tool whose inner try/except was removed."""

    def __init__(self) -> None:
        super().__init__(
            name="explode",
            description="Always raises.",
            parameters={"type": "object", "properties": {}},
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        raise RuntimeError("boom")


async def test_tool_exception_becomes_execution_error_result() -> None:
    executor = ToolExecutor()
    executor.register_tool(_ExplodingTool())

    result = await executor.execute("explode", {})  # must not raise

    assert result.success is False
    assert result.data == {}
    assert result.error == "Execution error: boom"


async def test_tool_exception_does_not_break_sibling_calls() -> None:
    """A failed call leaves the executor fully usable for the next call."""
    executor = ToolExecutor()
    executor.register_tool(_ExplodingTool())

    failed = await executor.execute("explode", {})
    missing = await executor.execute("nope", {})

    assert failed.error is not None and failed.error.startswith("Execution error:")
    assert missing.success is False
    assert missing.error == "Tool not found: nope"
