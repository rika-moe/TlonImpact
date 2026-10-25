"""Model requests own their timeout while inherited execution deadlines stay authoritative."""

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.program_helpers import make_program
from tlon.agents.member_runtime import MemberRuntime, MemberServices
from tlon.agents.program_store import ProgramStore
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.config import AgentConfig, LLMConfig
from tlon.base.events.event_bus import EventBus
from tlon.base.execution import ExecutionDeadlineError, ExecutionScope, MemberWriteGate
from tlon.base.llm_client import LLMClient
from tlon.base.paths import TlonPaths
from tlon.memory.models import MemoryConfig
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.model import make_model_operations
from tlon.operations.schema import UnknownOutcomeError

ARGUMENTS = {"messages": [{"role": "user", "content": "hello"}]}


def raw_response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="done", tool_calls=None, reasoning_content=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr("litellm.token_counter", lambda **kwargs: 1)
    provider = AsyncMock(return_value=raw_response())
    monkeypatch.setattr("litellm.acompletion", provider)
    return provider


def executor_at(
    timeout: float, deadline: float | None = None
) -> tuple[OperationExecutor, LLMClient]:
    client = LLMClient(
        LLMConfig(
            model_name="openai/test",
            max_output_tokens=4096,
            context_window=128000,
            request_timeout=timeout,
        )
    )
    store = ProgramStore(None, build_id="test")
    scope = ExecutionScope(store, "run", 100000, "output", deadline=deadline)
    executor = OperationExecutor(
        OperationRegistry(
            make_model_operations(
                client, AsyncMock(return_value="persona"), request_timeout=timeout
            )
        ),
        CallContext("member", "run", "call", execution=scope),
        store,
        default_timeout=0.05,
    )
    return executor, client


async def test_runtime_model_exceeds_tool_cap_but_ordinary_tool_still_times_out(
    tmp_path: Path,
    provider: AsyncMock,
) -> None:
    async def slow_provider(**kwargs: Any) -> SimpleNamespace:
        await asyncio.sleep(0.35)
        return raw_response()

    provider.side_effect = slow_provider
    client = LLMClient(
        LLMConfig(
            model_name="openai/test",
            max_output_tokens=4096,
            context_window=128000,
            request_timeout=3.0,
        )
    )
    memory = MagicMock(storage_path=tmp_path / "memory", config=MemoryConfig())

    async def slow_tool(context: CallContext, arguments: dict[str, Any]) -> None:
        await asyncio.sleep(10)

    services = MemberServices(
        config=AgentConfig(id="member", name="Member"),
        llm_client=client,
        memory=memory,
        task_manager=MagicMock(),
        tool_executor=ToolExecutor(),
        tlon_paths=TlonPaths(home=tmp_path),
        write_gate=MemberWriteGate(),
        per_tool_timeout=0.15,
        operations=(
            Operation(
                "slow-tool",
                "test",
                {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                True,
                slow_tool,
                effects=frozenset({"write"}),
            ),
        ),
    )
    member = MemberRuntime(
        member_id="member",
        name="Member",
        event_bus=EventBus(),
        spec=make_program(),
        builtins=make_member_builtins(),
        services=services,
    )
    # Frozen host context avoids persona filesystem setup for this request.
    member._program_store.freeze_model_context(
        "run", "host", {"system_prompt": "persona", "tools": []}
    )
    scope = ExecutionScope(member._program_store, "run", 100000, "output")
    context = CallContext("member", "run", "model", execution=scope)
    try:
        result = await member._operation_executor.call("model-call", ARGUMENTS, context)
        assert result["content"] == "done"
        assert provider.await_count == 1
        assert 0.15 < provider.await_args.kwargs["timeout"] <= 3.0
        with pytest.raises(UnknownOutcomeError):
            await member._operation_executor.call("slow-tool", {}, replace(context, call_id="tool"))
    finally:
        member._program_store.close()


@pytest.mark.parametrize("model_timeout,parent_timeout", [(0.25, None), (3.0, 0.25)])
async def test_model_and_shorter_parent_deadlines_leave_unknown_without_retry(
    provider: AsyncMock,
    model_timeout: float,
    parent_timeout: float | None,
) -> None:
    async def blocked_provider(**kwargs: Any) -> SimpleNamespace:
        await asyncio.Event().wait()
        return raw_response()

    provider.side_effect = blocked_provider
    executor, _ = executor_at(
        model_timeout, time.time() + parent_timeout if parent_timeout else None
    )
    with pytest.raises(UnknownOutcomeError):
        await executor.call("model-call", ARGUMENTS)
    assert provider.await_count == 1
    effective = provider.await_args.kwargs["timeout"]
    assert 0 < effective <= (parent_timeout or model_timeout)
    with pytest.raises(UnknownOutcomeError):
        await executor.call("model-call", ARGUMENTS)
    assert provider.await_count == 1
    state = executor.store.lookup_model_path("run", ("operation:call", "model:0"))
    assert state is not None and state["status"] == "unknown"


async def test_expired_scope_never_dispatches_provider(provider: AsyncMock) -> None:
    executor, _ = executor_at(3, time.time() - 1)
    with pytest.raises(ExecutionDeadlineError):
        await executor.call("model-call", ARGUMENTS)
    provider.assert_not_awaited()


async def test_committed_model_recovers_after_deadline_without_another_request(
    provider: AsyncMock,
) -> None:
    executor, _ = executor_at(3)
    commit = executor._commit

    async def lose_outer_receipt(*args: Any) -> None:
        raise asyncio.CancelledError()

    executor._commit = lose_outer_receipt
    with pytest.raises(asyncio.CancelledError):
        await executor.call("model-call", ARGUMENTS)
    executor._commit = commit
    expired = ExecutionScope(executor.store, "run", 100000, "output", deadline=time.time() - 1)
    context = replace(executor.context, execution=expired)
    result = await executor.call("model-call", ARGUMENTS, context)
    assert result["content"] == "done"
    provider.assert_awaited_once()
