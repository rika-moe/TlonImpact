"""Shared operation failures preserve the ToolExecutor tracing boundary."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tlon.agents.tool_executor import ToolExecutor
from tlon.base.execution import BudgetExceededError, ExecutionDeadlineError
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.schema import (
    InputError,
    OperationError,
    OutputError,
    RecoveryMismatchError,
    RejectedError,
    UnknownOutcomeError,
)
from tlosure.eval import EvalError


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


@pytest.mark.parametrize(
    "error",
    [
        asyncio.CancelledError(),
        BudgetExceededError("budget"),
        ExecutionDeadlineError("deadline"),
        RecoveryMismatchError("identity"),
        UnknownOutcomeError("uncertain", {}),
        OutputError("output"),
        OperationError("operation"),
        EvalError("evaluation"),
        RuntimeError("infra"),
        OSError("disk"),
    ],
)
async def test_tool_blocking_failures_propagate(error: BaseException) -> None:
    executor = _executor()
    tool = executor.get_tool("explode")
    tool._executor.call = AsyncMock(side_effect=error)  # type: ignore[method-assign]
    with pytest.raises(type(error)) as failure:
        await executor.execute("explode", {})
    assert failure.value is error


@pytest.mark.parametrize(
    "error", [InputError("bad input"), RejectedError("denied", {"reason": "policy"})]
)
async def test_model_correctable_rejection_returns_result(error: Exception) -> None:
    executor = _executor()
    executor.get_tool("explode")._executor.call = AsyncMock(side_effect=error)  # type: ignore[method-assign]
    result = await executor.execute("explode", {})
    assert not result.success
    assert result.error == f"{type(error).__name__}: {error}"
    assert result.data == getattr(error, "data", None)


async def test_real_infrastructure_failure_propagates() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        await _executor().execute("explode", {})


async def test_missing_tool_is_model_correctable() -> None:
    missing = await _executor().execute("nope", {})
    assert not missing.success
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
    assert definition["function"]["parameters"] == operation.input_schema
    definition["function"]["parameters"]["properties"].clear()
    assert "title" in tool.parameters["properties"]
