"""The two model entry points keep their owning client's prompt and ledger."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tlon.base.config import LLMConfig
from tlon.base.llm_client import LLMClient, LLMResponse
from tlon.base.paths import TlonPaths
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.model import make_model_operations
from tlon.operations.schema import UnknownOutcomeError
from tlosure.edn import loads
from tlosure.eval import Evaluator


async def test_model_entries_charge_only_the_bound_member(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    for member in ("a", "b"):
        client = LLMClient(
            LLMConfig(model_name="openai/test", max_output_tokens=4096, context_window=128000),
            agent_id=member,
            tlon_paths=paths,
        )
        client._chat_with_retries = AsyncMock(
            return_value=LLMResponse(
                content=member,
                tool_calls=[],
                usage={"prompt_tokens": 7, "completion_tokens": 3},
            )
        )
        system = AsyncMock(return_value=f"persona:{member}")
        executor = OperationExecutor(
            OperationRegistry(make_model_operations(client, system)),
            CallContext(member, f"run:{member}", f"call:{member}"),
        )
        if member == "a":
            adapter = DSLAdapter(executor)
            result = await Evaluator(adapter.builtins(), 1000, 100).eval_async(
                loads('(model-call {:messages [{:role "user" :content "input"}] :max_tokens 5})'),
                {},
                0,
                adapter.handle,
            )
            assert result["content"] == member
        else:
            result = await OperationTool(executor, "model-call").execute_context(
                {"messages": [{"role": "user", "content": "input"}], "max_tokens": 5}
            )
            assert result.success and result.data["content"] == member
        system.assert_awaited_once()
        records = [
            json.loads(line) for line in paths.agent_ledger_file(member).read_text().splitlines()
        ]
        assert len(records) == 1
        assert records[0]["completion_tokens"] == 3
        assert records[0]["run_id"] == f"run:{member}"
        assert records[0]["model"] == "openai/test"
    assert len(paths.agent_ledger_file("a").read_text().splitlines()) == 1


async def test_model_schema_rejects_identity_and_boolean_budget() -> None:
    client = AsyncMock()
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(client, AsyncMock())), CallContext("a", "r", "c")
    )
    tool = OperationTool(executor, "model-call")
    for arguments in (
        {"messages": [{"role": "user", "content": "x"}], "max_tokens": True},
        {"messages": [{"role": "user", "content": "x"}], "max_tokens": 2, "member_id": "b"},
        {"messages": [{"role": "system", "content": "override"}], "max_tokens": 2},
        {
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 2,
            "tools": [{"name": "custom"}],
        },
        {
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": 2,
            "api_base": "https://override",
        },
        {"prompt": "x", "max_tokens": 2},
    ):
        result = await tool.execute_context(arguments)
        assert not result.success and "InputError" in result.error
    client.chat.assert_not_awaited()


async def test_model_lost_response_reports_unknown_call_identity() -> None:
    client = AsyncMock()
    client.chat.side_effect = OSError("lost")
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(client, AsyncMock(return_value="system"))),
        CallContext("a", "r", "stable"),
    )
    with pytest.raises(UnknownOutcomeError) as failure:
        await executor.call(
            "model-call", {"messages": [{"role": "user", "content": "x"}], "max_tokens": 1}
        )
    assert failure.value.data == {
        "call_id": "stable",
        "outcome": "unknown",
        "retry_safe": False,
        "unknown_source": "model",
    }


async def test_tool_only_response_preserves_structure_without_execution() -> None:
    from tlon.base.llm_client import ToolCall

    client = AsyncMock()
    client.chat.return_value = LLMResponse(
        content=None,
        tool_calls=[ToolCall("t", "mail-send", {"content": "draft"})],
        finish_reason="tool_calls",
        reasoning_content="considered",
    )
    definitions = [{"type": "function", "function": {"name": "mail-send", "parameters": {}}}]
    executor = OperationExecutor(
        OperationRegistry(
            make_model_operations(client, AsyncMock(return_value="persona"), lambda: definitions)
        ),
        CallContext("a", "r", "c"),
    )
    result = await executor.call(
        "model-call",
        {
            "messages": [{"role": "user", "content": "draft"}],
            "tools": ["mail-send"],
            "max_tokens": 8,
        },
    )
    assert result["content"] is None
    assert result["tool_calls"] == [
        {"id": "t", "name": "mail-send", "arguments": {"content": "draft"}}
    ]
    assert result["finish_reason"] == "tool_calls"
    assert result["reasoning_content"] == "considered"
    assert client.chat.await_args.kwargs["tools"] == definitions


@pytest.mark.parametrize("measure", ["output", "total"])
@pytest.mark.parametrize("output_cap", [None, 8])
async def test_committed_model_restores_missing_outer_receipt_before_cancel_or_deadline(
    tmp_path: Path,
    measure: str,
    output_cap: int | None,
) -> None:
    import asyncio
    import time
    from dataclasses import replace

    from tlon.agents.program_store import ProgramStore
    from tlon.base.execution import ExecutionScope

    store = ProgramStore(tmp_path / "run.sqlite", build_id="test")
    client = LLMClient(
        LLMConfig(model_name="openai/test", max_output_tokens=4096, context_window=128000),
        agent_id="a",
        tlon_paths=TlonPaths(home=tmp_path),
    )
    client._chat_with_retries = AsyncMock(
        return_value=LLMResponse(
            content="done",
            usage={"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
        )
    )
    system = AsyncMock(return_value="frozen")
    operations = make_model_operations(client, system)
    scope = ExecutionScope(store, "r", 100, measure)
    context = CallContext("a", "r", "c", execution=scope)
    executor = OperationExecutor(OperationRegistry(operations), context, store)
    original = executor._commit

    async def lose_outer(*args: object) -> None:
        raise asyncio.CancelledError()

    executor._commit = lose_outer
    args = {"messages": [{"role": "user", "content": "x"}]}
    if output_cap is not None:
        args["max_tokens"] = output_cap
    with pytest.raises(asyncio.CancelledError):
        await executor.call("model-call", args)
    executor._commit = original
    system.side_effect = AssertionError("must not read changed persona")
    restored_scope = ExecutionScope(store, "r", 100, measure, deadline=time.time() - 1)
    restored_scope.cancelled.set()
    restored = replace(context, execution=restored_scope)
    found, result = await executor.replay("model-call", args, restored)
    assert found and result["content"] == "done"
    assert await executor.call("model-call", args, restored) == result
    client._chat_with_retries.assert_awaited_once()
    system.assert_awaited_once()


async def test_unknown_provider_outcome_is_not_resubmitted(tmp_path: Path) -> None:
    from tlon.agents.program_store import ProgramStore
    from tlon.base.execution import ExecutionScope

    store = ProgramStore(tmp_path / "run.sqlite", build_id="test")
    client = LLMClient(
        LLMConfig(model_name="openai/test", max_output_tokens=4096, context_window=128000),
        agent_id="a",
        tlon_paths=TlonPaths(home=tmp_path),
    )
    client._chat_with_retries = AsyncMock(side_effect=OSError("lost"))
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(client, AsyncMock(return_value="persona"))),
        CallContext("a", "r", "c", execution=ExecutionScope(store, "r", 100, "output")),
        store,
    )
    args = {"messages": [{"role": "user", "content": "x"}], "max_tokens": 8}
    for _ in range(2):
        with pytest.raises(UnknownOutcomeError):
            await executor.call("model-call", args)
    client._chat_with_retries.assert_awaited_once()


async def test_unknown_tool_is_rejected_before_model_dispatch() -> None:
    from tlon.operations.schema import InputError

    client = AsyncMock()
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(client, AsyncMock(return_value="persona"))),
        CallContext("a", "r", "c"),
    )
    with pytest.raises(InputError, match="Unknown model tools"):
        await executor.call(
            "model-call",
            {
                "messages": [{"role": "user", "content": "x"}],
                "tools": ["invented"],
                "max_tokens": 8,
            },
        )
    client.chat.assert_not_awaited()


async def test_frozen_system_and_catalog_replay_before_current_providers(tmp_path: Path) -> None:
    from tlon.agents.program_store import ProgramStore
    from tlon.base.execution import ExecutionScope, bind_execution
    from tlon.operations.model_service import ModelService

    store = ProgramStore(None, build_id="test")
    client = LLMClient(
        LLMConfig(model_name="openai/test", max_output_tokens=4096, context_window=128000),
        agent_id="a",
        tlon_paths=TlonPaths(home=tmp_path),
    )
    client._chat_with_retries = AsyncMock(return_value=LLMResponse(content="answer"))
    system = AsyncMock(return_value="original persona")
    catalog = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
    from unittest.mock import Mock

    tools = Mock(return_value=catalog)
    service = ModelService(client, system, tools)
    messages = [{"role": "user", "content": "x"}]
    with bind_execution(ExecutionScope(store, "r", 100, "output")):
        response = await service.request(messages, tools=["read"], max_tokens=8)
    system.side_effect = AssertionError("persona deleted")
    tools.side_effect = AssertionError("catalog deleted")
    with bind_execution(ExecutionScope(store, "r", 100, "output")):
        replay = await service.request(messages, tools=["read"], max_tokens=8)
    assert replay == response
    system.assert_awaited_once()
    client._chat_with_retries.assert_awaited_once()
    assert store.load_model_context("r", "host") == {
        "system_prompt": "original persona",
        "tools": catalog,
    }


async def test_same_scope_next_request_checks_revoked_tools(tmp_path: Path) -> None:
    from unittest.mock import Mock

    from tlon.agents.program_store import ProgramStore
    from tlon.base.execution import ExecutionScope, bind_execution
    from tlon.operations.model_service import ModelService
    from tlon.operations.schema import InputError

    store = ProgramStore(None, build_id="test")
    client = LLMClient(
        LLMConfig(model_name="openai/test", max_output_tokens=4096, context_window=128000),
        agent_id="a",
        tlon_paths=TlonPaths(home=tmp_path),
    )
    client._chat_with_retries = AsyncMock(return_value=LLMResponse(content="answer"))
    tools = Mock(
        return_value=[{"type": "function", "function": {"name": "read", "parameters": {}}}]
    )
    service = ModelService(client, AsyncMock(return_value="persona"), tools)
    messages = [{"role": "user", "content": "x"}]
    with bind_execution(ExecutionScope(store, "r", 100, "output")):
        await service.request(messages, tools=["read"], max_tokens=8)
        tools.return_value = []
        with pytest.raises(InputError, match="Unknown model tools"):
            await service.request(messages, tools=["read"], max_tokens=8)
    client._chat_with_retries.assert_awaited_once()


@pytest.mark.parametrize("measure", ["output", "total"])
async def test_recorded_rejection_restores_missing_outer_receipt_without_dispatch(
    tmp_path: Path,
    measure: str,
) -> None:
    import asyncio
    import time
    from dataclasses import replace

    from tlon.agents.program_store import ProgramStore
    from tlon.base.execution import ExecutionScope
    from tlon.base.llm_client import ContextWindowExceededError
    from tlon.operations.schema import RejectedError

    store = ProgramStore(tmp_path / "rejected.sqlite", build_id="test")
    client = LLMClient(
        LLMConfig(model_name="openai/test", max_output_tokens=4096, context_window=128000),
        agent_id="a",
        tlon_paths=TlonPaths(home=tmp_path),
    )
    client._chat_with_retries = AsyncMock(
        side_effect=ContextWindowExceededError("context too long")
    )
    system = AsyncMock(return_value="persona")
    context = CallContext("a", "r", "c", execution=ExecutionScope(store, "r", 100, measure))
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(client, system)), context, store
    )
    original = executor._commit

    async def lose_outer(*args: object) -> None:
        raise asyncio.CancelledError()

    executor._commit = lose_outer
    args = {"messages": [{"role": "user", "content": "x"}], "max_tokens": 8}
    with pytest.raises(asyncio.CancelledError):
        await executor.call("model-call", args)
    executor._commit = original
    system.side_effect = AssertionError("persona must not be read")
    expired = ExecutionScope(store, "r", 100, measure, deadline=time.time() - 1)
    expired.cancelled.set()
    recovered = replace(context, execution=expired)
    for replay in (True, False):
        with pytest.raises(RejectedError, match="context too long") as failure:
            if replay:
                await executor.replay("model-call", args, recovered)
            else:
                await executor.call("model-call", args, recovered)
        assert failure.value.data == {"call_id": "c", "outcome": "rejected"}
    assert store.budget_usage("r")["used"] == 0
    assert store.budget_usage("r")["pending_count"] == 0
    client._chat_with_retries.assert_awaited_once()
    system.assert_awaited_once()


@pytest.mark.parametrize("output_cap,expected", [(None, 12), (5, 5), (50, 12)])
async def test_optional_output_cap_uses_frozen_member_limit_and_shared_program_budget(
    tmp_path: Path, output_cap: int | None, expected: int
) -> None:
    from tlon.agents.program_store import ProgramStore
    from tlon.base.execution import ExecutionScope

    store = ProgramStore(tmp_path / "budget.sqlite", build_id="test")
    client = LLMClient(
        LLMConfig(model_name="openai/test", max_output_tokens=12, context_window=128000),
        agent_id="a",
        tlon_paths=TlonPaths(home=tmp_path),
    )
    provider = AsyncMock(return_value=LLMResponse("done", usage={"completion_tokens": 2}))
    client._chat_with_retries = provider
    scope = ExecutionScope(store, "r", 100, "output")
    executor = OperationExecutor(
        OperationRegistry(make_model_operations(client, AsyncMock(return_value="persona"))),
        CallContext("a", "r", "c", execution=scope),
        store,
    )
    arguments = {"messages": [{"role": "user", "content": "task"}]}
    if output_cap is not None:
        arguments["max_tokens"] = output_cap
    await executor.call("model-call", arguments)
    assert provider.await_args.args[4] == expected
    client.config.max_output_tokens = 1
    # A different invocation consumes the same program allowance but keeps
    # the model configuration frozen when the entry first sampled.
    await executor.call("model-call", arguments, CallContext("a", "r", "next", execution=scope))
    assert provider.await_args.args[4] == expected
    assert provider.await_count == 2
    # Output accounting settles the reliable completion usage.
    assert store.budget_usage("r")["used"] == 4
