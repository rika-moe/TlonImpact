"""Shared operation failures preserve the ToolExecutor tracing boundary."""

from typing import Any

from tlon.agents.tool_executor import ToolExecutor
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry


def _executor() -> ToolExecutor:
    def explode(context: CallContext, arguments: dict[str, Any]) -> Any:
        raise RuntimeError("boom")

    operation = Operation(
        "explode",
        "Always raises",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        True,
        explode,
    )
    execution = OperationExecutor(OperationRegistry([operation]), CallContext("a", "r", "c"))
    executor = ToolExecutor()
    executor.register_tool(OperationTool(execution, "explode"))
    return executor


async def test_tool_exception_becomes_execution_error_result() -> None:
    result = await _executor().execute("explode", {})
    assert result.success is False
    assert result.data == {}
    assert result.error == "Execution error: boom"


async def test_tool_exception_does_not_break_sibling_calls() -> None:
    executor = _executor()
    failed = await executor.execute("explode", {})
    missing = await executor.execute("nope", {})
    assert failed.error.startswith("Execution error:")
    assert missing.success is False
    assert missing.error == "Tool not found: nope"


def test_projection_definition_and_description_follow_operation() -> None:
    def identity(context: CallContext, arguments: dict[str, Any]) -> Any:
        return arguments

    operation = Operation(
        "describe-input",
        "Describes a required string and arbitrary JSON",
        {
            "type": "object",
            "properties": {"title": {"type": "string", "description": "标题"}, "value": True},
            "required": ["title"],
            "additionalProperties": False,
        },
        True,
        identity,
    )
    execution = OperationExecutor(OperationRegistry([operation]), CallContext("a", "r", "c"))
    tool = OperationTool(execution, operation.name)
    definition = tool.to_openai_definition()
    assert definition["function"]["name"] == operation.name
    assert definition["function"]["description"] == operation.description
    assert "- title (string) (required): 标题" in tool.get_parameter_description()
    assert "- value (JSON)" in tool.get_parameter_description()
    definition["function"]["parameters"]["properties"].clear()
    assert "title" in tool.parameters["properties"]
