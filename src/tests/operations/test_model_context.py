"""Window inspection uses frozen host inputs without sampling or spending tokens."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.base.execution import ExecutionScope, bind_execution
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.model import make_model_operations
from tlon.operations.schema import RecoveryMismatchError


@pytest.mark.parametrize("input_tokens", [40, 81, 100, 110])
@pytest.mark.parametrize("output_cap", [None, 50, 10])
async def test_context_inspection_counts_frozen_system_and_tools_and_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    input_tokens: int,
    output_cap: int | None,
) -> None:
    store = ProgramStore(tmp_path / "program.sqlite", build_id="test")
    catalog = [{"type": "function", "function": {"name": "time-now", "parameters": {}}}]
    store.freeze_model_context("r", "host", {"system_prompt": "original", "tools": catalog})
    store.freeze_model_context(
        "r",
        "config",
        {"model_name": "openai/original", "max_output_tokens": 20, "context_window": 100},
    )
    client = AsyncMock()
    counter = Mock(side_effect=[input_tokens - 10, 10])
    monkeypatch.setattr("litellm.token_counter", counter)
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(client, AsyncMock(side_effect=AssertionError()))),
        CallContext("a", "r", "inspect"),
        store,
    )
    arguments: dict[str, Any] = {
        "messages": [{"role": "user", "content": "task"}],
        "tools": ["time-now"],
    }
    if output_cap is not None:
        arguments["max_tokens"] = output_cap
    expected_output = min(20, max(100 - input_tokens, 0), output_cap or 20)
    with bind_execution(ExecutionScope(store, "r", 100, "output")):
        result = await executor.call("model-context", arguments)
    assert result == {
        "input_tokens": input_tokens,
        "output_tokens": expected_output,
        "context_window": 100,
        "fits": expected_output > 0,
    }
    assert counter.call_count == 2
    assert counter.call_args_list[0].kwargs == {
        "model": "openai/original",
        "messages": [{"role": "system", "content": "original"}, *arguments["messages"]],
    }
    assert counter.call_args_list[1].kwargs["model"] == "openai/original"
    assert json.loads(counter.call_args_list[1].kwargs["text"]) == catalog
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


@pytest.mark.parametrize("measure,budget,expected", [("output", 12, 12), ("total", 45, 5)])
async def test_context_inspection_clips_output_to_shared_run_resources(
    monkeypatch: pytest.MonkeyPatch,
    measure: str,
    budget: int,
    expected: int,
) -> None:
    store = ProgramStore(None, build_id="test")
    store.freeze_model_context(
        "r",
        "config",
        {"model_name": "openai/test", "max_output_tokens": 32768, "context_window": 100},
    )
    store.freeze_model_context("r", "host", {"system_prompt": "system", "tools": []})
    monkeypatch.setattr("litellm.token_counter", Mock(return_value=40))
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(AsyncMock(), AsyncMock())),
        CallContext("a", "r", "inspect"),
        store,
    )
    with bind_execution(ExecutionScope(store, "r", budget, measure)):
        result = await executor.call(
            "model-context", {"messages": [{"role": "user", "content": "task"}]}
        )
    assert result == {
        "input_tokens": 40,
        "context_window": 100,
        "output_tokens": expected,
        "fits": True,
    }
    assert store.budget_usage("r")["used"] == 0


async def test_context_inspection_reports_exhausted_budget_without_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tlon.base.execution import BudgetExceededError

    store = ProgramStore(None, build_id="test")
    store.freeze_model_context(
        "r",
        "config",
        {"model_name": "openai/test", "max_output_tokens": 32768, "context_window": 100},
    )
    store.freeze_model_context("r", "host", {"system_prompt": "system", "tools": []})
    monkeypatch.setattr("litellm.token_counter", Mock(return_value=40))
    client = AsyncMock()
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(client, AsyncMock())),
        CallContext("a", "r", "inspect"),
        store,
    )
    with (
        bind_execution(ExecutionScope(store, "r", 40, "total")),
        pytest.raises(BudgetExceededError),
    ):
        await executor.call("model-context", {"messages": [{"role": "user", "content": "task"}]})
    client.chat.assert_not_awaited()
