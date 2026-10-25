"""Durable shared model allowances cover recovery, contention and uncertain dispatch."""

import asyncio
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.base.config import LLMConfig
from tlon.base.execution import (
    BudgetExceededError,
    ExecutionDeadlineError,
    ExecutionScope,
    ModelRequestRejectedError,
    UnknownModelOutcomeError,
    bind_execution,
)
from tlon.base.llm_client import ContextWindowExceededError, LLMClient, LLMResponse
from tlon.operations.schema import RecoveryMismatchError


@pytest.fixture(params=[False, True], ids=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ProgramStore:
    return ProgramStore(tmp_path / "budget.sqlite" if request.param else None, build_id="test")


def response(actual: int | None = None) -> dict[str, Any]:
    return LLMResponse(
        "answer", usage={"total_tokens": actual} if actual is not None else None
    ).to_dict()


async def test_output_releases_unused_allowance_and_replay_is_free(store: ProgramStore) -> None:
    calls: list[int] = []

    async def execute(allowed: int) -> dict[str, Any]:
        calls.append(allowed)
        return LLMResponse("answer", usage={"completion_tokens": 1}).to_dict()

    for _ in range(2):
        scope = ExecutionScope(store, "root", 5, "output")
        for question in range(5):
            await scope.model_call({"question": question}, 4, execute)
        with pytest.raises(BudgetExceededError):
            await scope.model_call({"question": 5}, 4, execute)
    assert calls == [4, 4, 3, 2, 1]
    assert store.budget_usage("root")["used"] == 5


async def test_total_settlement_frees_unused_estimate_and_counts_nested_calls(
    store: ProgramStore,
) -> None:
    scope = ExecutionScope(store, "session", 50, "total")
    execute = AsyncMock(return_value=response(8))
    await scope.child("stage").model_call({}, 10, execute, estimated_input=20)
    await scope.child("compress").model_call({}, 10, execute, estimated_input=20)
    await scope.child("tool/model").model_call({}, 10, execute, estimated_input=20)
    assert store.budget_usage("session")["used"] == 24
    assert execute.await_count == 3


async def test_concurrent_reservations_never_exceed_allowance(store: ProgramStore) -> None:
    scope = ExecutionScope(store, "session", 5, "output")
    entered, release = asyncio.Event(), asyncio.Event()
    outputs: list[int] = []

    async def execute(allowed: int) -> dict[str, Any]:
        outputs.append(allowed)
        entered.set()
        await release.wait()
        return response()

    first = asyncio.create_task(scope.child("first").model_call({}, 4, execute))
    await entered.wait()
    second = asyncio.create_task(scope.child("second").model_call({}, 4, execute))
    await asyncio.sleep(0.01)
    assert store.budget_usage("session")["used"] <= 5
    release.set()
    await asyncio.gather(first, second)
    assert sorted(outputs) == [1, 4]
    assert store.budget_usage("session")["used"] == 5


async def test_unknown_and_missing_usage_retain_reservation(store: ProgramStore) -> None:
    execute = AsyncMock(side_effect=OSError("provider response lost"))
    scope = ExecutionScope(store, "unknown", 5, "output")
    with pytest.raises(UnknownModelOutcomeError):
        await scope.model_call({}, 4, execute)
    with pytest.raises(UnknownModelOutcomeError):
        await ExecutionScope(store, "unknown", 5, "output").model_call({}, 4, execute)
    assert execute.await_count == 1
    assert store.budget_usage("unknown")["used"] == 4
    missing = ExecutionScope(store, "missing", 30, "total")
    await missing.model_call({}, 10, AsyncMock(return_value=response()), estimated_input=5)
    assert store.budget_usage("missing")["used"] == 15


async def test_real_usage_above_estimate_is_recorded_and_stops_later_calls(
    store: ProgramStore,
) -> None:
    scope = ExecutionScope(store, "session", 20, "total")
    await scope.model_call({}, 5, AsyncMock(return_value=response(25)), estimated_input=1)
    assert store.budget_usage("session")["used"] == 25
    with pytest.raises(BudgetExceededError):
        await scope.model_call({}, 5, AsyncMock(return_value=response(1)), estimated_input=1)


async def test_rejected_request_refunds_and_replays_rejection(store: ProgramStore) -> None:
    execute = AsyncMock(
        side_effect=ModelRequestRejectedError("context rejected", "ContextWindowExceededError")
    )
    for _ in range(2):
        with pytest.raises(ModelRequestRejectedError, match="context rejected"):
            await ExecutionScope(store, "root", 5, "output").model_call({}, 5, execute)
    assert execute.await_count == 1
    assert store.budget_usage("root")["used"] == 0


async def test_persisted_response_replays_after_cancellation(store: ProgramStore) -> None:
    execute = AsyncMock(return_value=response())
    await ExecutionScope(store, "root", 5, "output").model_call({}, 5, execute)
    recovered = ExecutionScope(store, "root", 5, "output")
    recovered.cancel()
    assert await recovered.model_call({}, 5, execute) == response()
    with pytest.raises(asyncio.CancelledError):
        await recovered.model_call({"new": True}, 1, execute)
    assert execute.await_count == 1


async def test_frozen_deadline_restores_and_expired_new_request_has_no_intent(
    store: ProgramStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    deadline = time.time() + 100
    execute = AsyncMock(return_value=response())
    await ExecutionScope(store, "root", 5, "output", deadline=deadline).model_call({}, 1, execute)
    monkeypatch.setattr("tlon.base.execution.time.time", lambda: deadline + 1)
    recovered = ExecutionScope(store, "root", 5, "output")
    assert await recovered.model_call({}, 1, execute) == response()
    assert recovered.deadline == deadline
    with pytest.raises(ExecutionDeadlineError):
        await recovered.model_call({"new": True}, 1, execute)
    assert (
        store.peek_model("root", ("model:1",), '{"output_limit":1,"request":{"new":true}}') is None
    )


async def test_budget_and_model_identity_cannot_change(store: ProgramStore) -> None:
    execute = AsyncMock(return_value=response())
    await ExecutionScope(store, "root", 5, "output").model_call({"prompt": "one"}, 1, execute)
    with pytest.raises(RecoveryMismatchError):
        await ExecutionScope(store, "root", 6, "output").model_call({"prompt": "one"}, 1, execute)
    with pytest.raises(RecoveryMismatchError):
        await ExecutionScope(store, "root", 5, "output").model_call({"prompt": "two"}, 1, execute)


async def test_child_session_cancellation_does_not_cancel_program(store: ProgramStore) -> None:
    parent = ExecutionScope(store, "program", 5, "output")
    session = ExecutionScope(store, "session", 20, "total", parent=parent)
    session.cancel()
    parent.check()
    with pytest.raises(asyncio.CancelledError):
        session.check()
    another = ExecutionScope(store, "another", 20, "total", parent=parent)
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        another.check()


async def test_llm_client_gateway_returns_persisted_model_response(store: ProgramStore) -> None:
    client = LLMClient(
        LLMConfig(model_name="openai/test", max_output_tokens=5, context_window=128000)
    )
    client._chat_with_retries = AsyncMock(return_value=LLMResponse("answer", usage=None))
    for _ in range(2):
        with bind_execution(ExecutionScope(store, "root", 5, "output")):
            result = await client.chat("system", [{"role": "user", "content": "question"}])
        assert result.content == "answer"
    assert client._chat_with_retries.await_count == 1
    assert store.budget_usage("root")["used"] == 5


async def test_llm_context_rejection_type_survives_replay(store: ProgramStore) -> None:
    client = LLMClient(
        LLMConfig(model_name="openai/test", max_output_tokens=5, context_window=128000)
    )
    client._chat_with_retries = AsyncMock(side_effect=ContextWindowExceededError("too large"))
    for _ in range(2):
        with (
            bind_execution(ExecutionScope(store, "root", 5, "output")),
            pytest.raises(ContextWindowExceededError),
        ):
            await client.chat("system", [])
    assert client._chat_with_retries.await_count == 1
    assert store.budget_usage("root")["used"] == 0


async def test_disk_restart_restores_usage_response_and_deadline(tmp_path: Path) -> None:
    database = tmp_path / "budget.sqlite"
    store = ProgramStore(database, build_id="test")
    deadline = time.time() + 100
    execute = AsyncMock(return_value=response(7))
    await ExecutionScope(store, "session", 50, "total", deadline=deadline).model_call(
        {}, 10, execute, estimated_input=10
    )
    recovered = ProgramStore(database, build_id="test")
    scope = ExecutionScope(recovered, "session", 50, "total")
    assert await scope.model_call({}, 10, execute, estimated_input=10) == response(7)
    assert scope.deadline == deadline
    assert recovered.budget_usage("session")["used"] == 7
    assert execute.await_count == 1


async def test_model_commit_failure_keeps_reservation_and_refuses_resend(
    store: ProgramStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    execute = AsyncMock(return_value=response(3))
    original = store.commit_model

    def failed(*args: Any) -> dict[str, Any]:
        raise OSError("disk unavailable")

    monkeypatch.setattr(store, "commit_model", failed)
    with pytest.raises(UnknownModelOutcomeError):
        await ExecutionScope(store, "root", 5, "output").model_call({}, 4, execute)
    monkeypatch.setattr(store, "commit_model", original)
    with pytest.raises(UnknownModelOutcomeError):
        await ExecutionScope(store, "root", 5, "output").model_call({}, 4, execute)
    assert store.budget_usage("root")["used"] == 4
    assert execute.await_count == 1


async def test_ledger_failure_keeps_known_response_and_does_not_resend(store: ProgramStore) -> None:
    client = LLMClient(
        LLMConfig(model_name="openai/test", max_output_tokens=5, context_window=128000)
    )
    client._chat_with_retries = AsyncMock(
        return_value=LLMResponse("committed", usage={"completion_tokens": 3})
    )
    client._record_usage = AsyncMock(side_effect=OSError("ledger unavailable"))
    for _ in range(2):
        with bind_execution(ExecutionScope(store, "root", 5, "output")):
            assert (await client.chat("system", [])).content == "committed"
    assert client._chat_with_retries.await_count == 1
    assert store.budget_usage("root")["used"] == 3


async def test_scoped_provider_timeout_is_not_automatically_retried(
    store: ProgramStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from litellm.exceptions import Timeout

    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    provider = AsyncMock(side_effect=Timeout("lost", model="test", llm_provider="openai"))
    monkeypatch.setattr("tlon.base.llm_client.litellm.acompletion", provider)
    client = LLMClient(
        LLMConfig(
            model_name="openai/test", max_output_tokens=5, context_window=128000, max_retries=3
        )
    )
    for _ in range(2):
        with (
            bind_execution(ExecutionScope(store, "root", 5, "output")),
            pytest.raises(UnknownModelOutcomeError),
        ):
            await client.chat("system", [])
    assert provider.await_count == 1
    assert store.budget_usage("root")["used"] == 5


async def test_llm_total_gateway_accounts_all_calls_under_same_scope(store: ProgramStore) -> None:
    client = LLMClient(
        LLMConfig(model_name="openai/test", max_output_tokens=5, context_window=128000)
    )
    client._chat_with_retries = AsyncMock(
        return_value=LLMResponse("answer", usage={"prompt_tokens": 3, "completion_tokens": 2})
    )
    root = ExecutionScope(store, "session", 100, "total")
    for role in ("stage", "compress", "tool"):
        with bind_execution(root.child(role)):
            await client.chat("system", [{"role": "user", "content": "question"}])
    assert store.budget_usage("session")["used"] == 15
    assert client._chat_with_retries.await_count == 3


async def test_same_root_child_cancellation_only_affects_descendants(store: ProgramStore) -> None:
    parent = ExecutionScope(store, "program", 5, "output")
    child = parent.child("operation")
    descendant = child.child("nested")
    child.cancel()
    parent.check()
    with pytest.raises(asyncio.CancelledError):
        descendant.check()


async def test_result_commit_and_reconciliation_failure_still_reports_unknown(
    store: ProgramStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    execute = AsyncMock(return_value=response())
    peek = store.peek_model

    def failed_commit(*args: Any) -> dict[str, Any]:
        raise OSError("result storage unavailable")

    calls = 0

    def failed_reconciliation(*args: Any) -> dict[str, Any] | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            return peek(*args)
        raise OSError("receipt storage unavailable")

    monkeypatch.setattr(store, "commit_model", failed_commit)
    monkeypatch.setattr(store, "peek_model", failed_reconciliation)
    with pytest.raises(UnknownModelOutcomeError, match="durably committed"):
        await ExecutionScope(store, "root", 5, "output").model_call({}, 4, execute)
    assert store.budget_usage("root")["used"] == 4


@pytest.mark.parametrize("measure", ["output", "total"])
async def test_receipt_and_settlement_rollback_together_on_commit_fault(
    store: ProgramStore, monkeypatch: pytest.MonkeyPatch, measure: str
) -> None:
    original = store._commit

    def failed(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        raise OSError("failure after receipt write before settlement")

    monkeypatch.setattr(store, "_commit", failed)
    with pytest.raises(UnknownModelOutcomeError):
        await ExecutionScope(store, "root", 30, measure).model_call(
            {},
            10,
            AsyncMock(return_value={"usage": {"completion_tokens": 3, "total_tokens": 3}}),
            estimated_input=5,
        )
    assert store.budget_usage("root")["used"] == (10 if measure == "output" else 15)
    assert store.peek_model("root", ("model:0",), '{"output_limit":10,"request":{}}') == {
        "status": "unknown"
    }


async def test_already_committed_model_response_survives_wrapper_failure(
    store: ProgramStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = store.commit_model

    def failed(*args: Any, **kwargs: Any) -> dict[str, Any]:
        original(*args, **kwargs)
        raise OSError("return lost after transaction")

    monkeypatch.setattr(store, "commit_model", failed)
    assert await ExecutionScope(store, "root", 30, "total").model_call(
        {}, 10, AsyncMock(return_value=response(3)), estimated_input=5
    ) == response(3)
    assert store.budget_usage("root")["used"] == 3


async def test_real_assembled_tool_schemas_with_boolean_values_are_counted_as_text(
    store: ProgramStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.program_helpers import make_program
    from tlon.agents.assembly import assemble_member_services
    from tlon.agents.member_runtime import MemberRuntime
    from tlon.agents.vocabulary import make_member_builtins
    from tlon.base.config import AgentConfig, DockerRuntimeConfig
    from tlon.base.events.event_bus import EventBus
    from tlon.base.paths import TlonPaths

    config = AgentConfig(
        id="counter",
        name="Counter",
        llm=LLMConfig(model_name="openai/gpt-4o-mini", max_output_tokens=5, context_window=128000),
        docker=DockerRuntimeConfig(level="L0"),
    )
    paths = TlonPaths(home=tmp_path / "member")
    monkeypatch.setattr(
        "tlon.agents.assembly.policy_trust_provider", lambda _paths: AsyncMock(return_value=True)
    )
    services = await assemble_member_services(
        config,
        registry=None,
        agent_info=AsyncMock(),
        tlon_paths=paths,
    )
    member = MemberRuntime(
        member_id=config.id,
        name=config.name,
        event_bus=EventBus(),
        spec=make_program(),
        builtins=make_member_builtins(),
        services=services,
    )
    tools = services.tool_executor.get_tool_definitions()
    state_put = next(tool for tool in tools if tool["function"]["name"] == "state-put")
    assert state_put["function"]["parameters"]["properties"]["value"] is True
    assert len(tools) > 30
    # The ordinary context estimator must accept that same real catalog.
    from tlon.operations.core import CallContext
    from tlon.operations.model_context import context_operation
    from tlon.operations.model_service import ModelService

    context_scope = ExecutionScope(store, "estimate", 100000, "output")
    store.freeze_model_context("estimate", "config", services.llm_client.configuration_snapshot())
    estimator = context_operation(
        ModelService(services.llm_client, AsyncMock(return_value="system"), lambda: tools),
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    )
    with bind_execution(context_scope):
        size = await estimator.handler(
            CallContext("counter", "estimate", "context"),
            {
                "messages": [{"role": "user", "content": "question"}],
                "tools": [tool["function"]["name"] for tool in tools],
                "max_tokens": 5,
            },
        )
    assert size["input_tokens"] > 0 and size["fits"] is True
    client = services.llm_client
    client._chat_with_retries = AsyncMock(
        return_value=LLMResponse("answer", usage={"total_tokens": 8})
    )
    with bind_execution(ExecutionScope(store, "session", 100000, "total")):
        result = await client.chat("system", [{"role": "user", "content": "question"}], tools=tools)
    assert result.content == "answer"
    assert client._chat_with_retries.await_count == 1
    assert store.budget_usage("session")["used"] == 8
    assert store.budget_usage("session")["pending_count"] == 0
    member._program_store.close()


async def test_pending_count_distinguishes_unknown_from_committed_response(
    store: ProgramStore,
) -> None:
    scope = ExecutionScope(store, "session", 30, "output")
    with pytest.raises(UnknownModelOutcomeError):
        await scope.child("lost").model_call({}, 5, AsyncMock(side_effect=OSError("lost")))
    assert store.budget_usage("session")["pending_count"] == 1
    await scope.child("done").model_call({}, 5, AsyncMock(return_value=response()))
    assert store.budget_usage("session")["pending_count"] == 1


async def test_call_deadline_does_not_rewrite_root_budget_and_history_still_replays(
    store: ProgramStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    root = ExecutionScope(store, "bounded-root", 20, "output", deadline=now + 100)
    child = root.child("operation", deadline=now + 2)
    await child.initialize()
    execute = AsyncMock(return_value=response())
    await child.model_call({}, 4, execute)
    assert store.budget_usage("bounded-root")["deadline"] == now + 100
    monkeypatch.setattr("tlon.base.execution.time.time", lambda: now + 3)
    with pytest.raises(ExecutionDeadlineError):
        child.check()
    root.check()
    replay = root.child("operation", deadline=now + 2)
    assert (await replay.model_call({}, 4, execute))["content"] == "answer"
    execute.assert_awaited_once()
    assert store.budget_usage("bounded-root")["used"] == 4


async def test_child_cannot_extend_frozen_root_deadline(store: ProgramStore) -> None:
    now = time.time()
    root = ExecutionScope(store, "expired-root", 20, "output", deadline=now - 1)
    await root.initialize()
    child = root.child("operation", deadline=now + 100)
    await child.initialize()
    with pytest.raises(ExecutionDeadlineError):
        child.check()
    assert store.budget_usage("expired-root")["deadline"] == now - 1


@pytest.mark.parametrize("actual", [None, True, -1, 1.5, "2"])
async def test_output_missing_or_invalid_usage_keeps_full_reservation(
    store: ProgramStore, actual: Any
) -> None:
    result = {"content": "answer", "usage": {"completion_tokens": actual}}
    execute = AsyncMock(return_value=result)
    for _ in range(2):
        scope = ExecutionScope(store, "invalid-usage", 8, "output")
        assert await scope.model_call({}, 6, execute) == result
    assert store.budget_usage("invalid-usage")["used"] == 6
    assert execute.await_count == 1


@pytest.mark.parametrize("actual", [0, 3, 25])
async def test_output_settles_exact_actual_including_zero_and_overage(
    store: ProgramStore, actual: int
) -> None:
    result = {"content": "answer", "usage": {"completion_tokens": actual}}
    execute = AsyncMock(return_value=result)
    for _ in range(2):
        scope = ExecutionScope(store, "actual-usage", 20, "output")
        assert await scope.model_call({}, 12, execute) == result
    assert execute.await_count == 1
    assert store.budget_usage("actual-usage")["used"] == actual
    if actual > 20:
        with pytest.raises(BudgetExceededError):
            await scope.model_call({"next": True}, 1, execute)
        assert execute.await_count == 1


async def test_output_concurrent_settlement_releases_only_completed_reservations(
    store: ProgramStore,
) -> None:
    scope = ExecutionScope(store, "concurrent-refund", 10, "output")
    entered, release = asyncio.Event(), asyncio.Event()

    async def pending(allowed: int) -> dict[str, Any]:
        assert allowed == 6
        entered.set()
        await release.wait()
        return {"usage": {"completion_tokens": 2}}

    task = asyncio.create_task(scope.child("pending").model_call({}, 6, pending))
    await entered.wait()
    settled = AsyncMock(return_value={"usage": {"completion_tokens": 1}})
    await scope.child("settled").model_call({}, 8, settled)
    settled.assert_awaited_once_with(4)
    # The uncommitted provider still owns six tokens; only the settled
    # request's unused three tokens become available to another request.
    assert store.budget_usage("concurrent-refund")["used"] == 7
    again = AsyncMock(return_value={"usage": {"completion_tokens": 1}})
    await scope.child("again").model_call({}, 8, again)
    again.assert_awaited_once_with(3)
    release.set()
    await task
    assert store.budget_usage("concurrent-refund")["used"] == 4
    assert store.budget_usage("concurrent-refund")["pending_count"] == 0


@pytest.mark.parametrize("measure", ["output", "total"])
def test_store_settlement_replay_never_charges_twice(store: ProgramStore, measure: str) -> None:
    store.ensure_budget("store-root", 30, measure)
    request = '{"request":"same"}'
    first = store.reserve_model("store-root", ("model:0",), request, 20, 3)
    assert first["output_limit"] == 20
    result = {"usage": {"completion_tokens": 2, "total_tokens": 5}}
    actual = 2 if measure == "output" else 5
    assert store.commit_model("store-root", ("model:0",), result, actual) == result
    assert store.commit_model("store-root", ("model:0",), result, actual) == result
    replay = store.reserve_model("store-root", ("model:0",), request, 20, 3)
    assert replay["status"] == "replay"
    assert store.budget_usage("store-root")["used"] == actual
    assert store.budget_usage("store-root")["pending_count"] == 0


@pytest.mark.parametrize("invalid", [True, -1, 1.5, "2"])
def test_store_rejects_invalid_settlement_without_releasing_unknown_reservation(
    store: ProgramStore, invalid: Any
) -> None:
    store.ensure_budget("invalid-settlement", 10, "output")
    store.reserve_model("invalid-settlement", ("model:0",), "{}", 8, 0)
    with pytest.raises(ValueError, match="Invalid actual model usage"):
        store.commit_model("invalid-settlement", ("model:0",), {}, invalid)
    assert store.budget_usage("invalid-settlement")["used"] == 8
    assert store.budget_usage("invalid-settlement")["pending_count"] == 1
    store.reject_model("invalid-settlement", ("model:0",), "Rejected", "known rejection")
    assert store.budget_usage("invalid-settlement")["used"] == 0
    assert store.budget_usage("invalid-settlement")["pending_count"] == 0


def test_sqlite_output_settlement_survives_reopen_without_double_charge(tmp_path: Path) -> None:
    database = tmp_path / "reopened.sqlite"
    request = '{"request":"persisted"}'
    result = {"usage": {"completion_tokens": 3}}
    store = ProgramStore(database, build_id="test")
    try:
        store.ensure_budget("restart", 100, "output")
        store.reserve_model("restart", ("model:0",), request, 90, 0)
        store.commit_model("restart", ("model:0",), result, 3)
    finally:
        store.close()
    recovered = ProgramStore(database, build_id="test")
    try:
        recovered.ensure_budget("restart", 100, "output")
        assert recovered.reserve_model("restart", ("model:0",), request, 90, 0) == {
            "status": "replay",
            "response": result,
        }
        recovered.commit_model("restart", ("model:0",), result, 3)
        assert recovered.budget_usage("restart")["used"] == 3
        assert recovered.reserve_model("restart", ("model:1",), "{}", 90, 0)["output_limit"] == 90
    finally:
        recovered.close()
