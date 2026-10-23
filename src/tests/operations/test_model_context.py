"""Window inspection uses frozen host inputs without sampling or spending tokens."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.base.execution import ExecutionScope, bind_execution
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.model import make_model_operations
from tlon.operations.schema import RecoveryMismatchError


@pytest.mark.parametrize("input_tokens,fits", [(40, True), (81, False)])
async def test_context_inspection_counts_frozen_system_and_tools_and_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, input_tokens: int, fits: bool
) -> None:
    store = ProgramStore(tmp_path / "program.sqlite", build_id="test")
    catalog = [{"type": "function", "function": {"name": "time-now", "parameters": {}}}]
    store.freeze_model_context("r", "host", {"system_prompt": "original", "tools": catalog})
    store.freeze_model_context(
        "r", "config", {"model_name": "openai/original", "max_tokens": 20, "context_window": 100}
    )
    client = AsyncMock()
    counter = Mock(return_value=input_tokens)
    monkeypatch.setattr("litellm.token_counter", counter)
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(client, AsyncMock(side_effect=AssertionError()))),
        CallContext("a", "r", "inspect"),
        store,
    )
    arguments: dict[str, Any] = {
        "messages": [{"role": "user", "content": "task"}],
        "tools": ["time-now"],
        "max_tokens": 50,
    }
    with bind_execution(ExecutionScope(store, "r", 100, "output")):
        result = await executor.call("model-context", arguments)
    assert result == {
        "input_tokens": input_tokens,
        "output_tokens": 20,
        "context_window": 100,
        "fits": fits,
    }
    counter.assert_called_once_with(
        model="openai/original",
        messages=[{"role": "system", "content": "original"}, *arguments["messages"]],
        tools=catalog,
    )
    counter.side_effect = AssertionError("history must not be resampled")
    with bind_execution(ExecutionScope(store, "r", 100, "output")):
        assert await executor.call("model-context", arguments) == result
    client.chat.assert_not_awaited()
    assert store.budget_usage("r")["used"] == 0


async def test_context_inspection_requires_frozen_configuration() -> None:
    store = ProgramStore(None, build_id="test")
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(AsyncMock(), AsyncMock(return_value="persona"))),
        CallContext("a", "r", "inspect"),
        store,
    )
    with (
        bind_execution(ExecutionScope(store, "r", 10, "output")),
        pytest.raises(RecoveryMismatchError, match="frozen model configuration"),
    ):
        await executor.call(
            "model-context", {"messages": [{"role": "user", "content": "task"}], "max_tokens": 1}
        )
