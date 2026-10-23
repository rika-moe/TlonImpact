"""DSL and actual model dispatch share timeout admission and durable receipts."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.recipe_helpers import recipe_for
from tlon.agents.context_pressure import ContextPressure
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.member_spec import MemberSpec
from tlon.agents.program_store import ProgramStore
from tlon.agents.session_journal import SessionJournal
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.trigger import AgentIdentity
from tlon.base.execution import (
    BudgetExceededError,
    ExecutionDeadlineError,
    ExecutionScope,
    bind_execution,
)
from tlon.base.llm_client import ToolCall
from tlon.base.paths import TlonPaths
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.schema import DefinitionError, UnknownOutcomeError
from tlon.tools.spill import ToolSpill
from tlosure.edn import loads
from tlosure.eval import Evaluator

SESSION_EDN = '{:version 1 :rules [{:name "respond" :on :mail-delivered :actions [(run-loop (quote {:context {:sections [(persona)]} :stages [(think) (act)] :until (>= (get state :iteration) (get limits :max_iterations)) :max-iterations 10 :max-consecutive-errors 3 :max-tokens 1000000 :reflect {:enabled false :interval-minutes 5}}))]}]}'


@pytest.fixture(params=[False, True], ids=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ProgramStore:
    return ProgramStore(tmp_path / "calls.sqlite" if request.param else None, build_id="test")


def operation(
    handler: Any,
    *,
    timeout: float | None = None,
    grace: float = 0,
    named: bool = False,
    safe: bool = False,
) -> Operation:
    properties = {"timeout": {"type": "number", "minimum": 0.001}} if named else {}
    return Operation(
        "slow-operation",
        "test",
        {"type": "object", "properties": properties, "required": [], "additionalProperties": False},
        {"type": "integer"},
        handler,
        effects=frozenset({"write"}),
        default_timeout=timeout,
        timeout_grace=grace,
        retry_safe=safe,
    )


def driver(tmp_path: Path, executor: OperationExecutor) -> LoopDriver:
    tools = ToolExecutor()
    tools.register_tool(OperationTool(executor, "slow-operation"))
    journal = SessionJournal("a", TlonPaths(home=tmp_path))
    return LoopDriver(
        identity=AgentIdentity("a", "A", "a@tlon.internal"),
        recipe=recipe_for(MemberSpec.from_edn(loads(SESSION_EDN)).rules[0]),
        services=MagicMock(),
        executor=MagicMock(),
        tool_executor=tools,
        journal=journal,
        spill=ToolSpill(TlonPaths(home=tmp_path), "a"),
        pressure=ContextPressure(MagicMock(), journal, 262144),
        is_human_sender=lambda _: True,
    )


async def invoke(
    entry: str, executor: OperationExecutor, tmp_path: Path, arguments: dict[str, Any] | None = None
) -> Any:
    arguments = arguments or {}
    context = CallContext("a", entry, entry)
    if entry == "tool":
        _, result = await driver(tmp_path, executor).execute_one_tool(
            entry,
            ToolCall("call", "slow-operation", arguments),
            context,
        )
        return result
    adapter = DSLAdapter(executor)
    from tlon.operations.schema import edn_value
    from tlosure.edn import dumps

    # Production DSL values are EDN maps, including named timeout parameters.
    source = loads(f"(slow-operation {dumps(edn_value(arguments))})")
    return await Evaluator(adapter.builtins(), 1000, 100).eval_async(
        source,
        {},
        0,
        lambda call: adapter.handle(call, context),
    )


@pytest.mark.parametrize("entry", ["dsl", "tool"])
async def test_same_default_timeout_applies_to_both_entries(
    store: ProgramStore, tmp_path: Path, entry: str
) -> None:
    async def handler(context: CallContext, arguments: dict[str, Any]) -> int:
        await asyncio.sleep(0.1)
        return 7

    executor = OperationExecutor(
        OperationRegistry([operation(handler)]),
        CallContext("a", "member", "member"),
        store,
        default_timeout=0.01,
    )
    with pytest.raises(UnknownOutcomeError, match="after dispatch"):
        await invoke(entry, executor, tmp_path)


@pytest.mark.parametrize("entry", ["dsl", "tool"])
async def test_operation_and_explicit_limits_override_member_default(
    store: ProgramStore, tmp_path: Path, entry: str
) -> None:
    async def handler(context: CallContext, arguments: dict[str, Any]) -> int:
        await asyncio.sleep(0.02)
        return 7

    executor = OperationExecutor(
        OperationRegistry([operation(handler, timeout=0.1, named=True)]),
        CallContext("a", "member", "member"),
        store,
        default_timeout=0.001,
    )
    result = await invoke(entry, executor, tmp_path)
    assert result == 7 if entry == "dsl" else result.success and result.data == 7


@pytest.mark.parametrize("entry", ["dsl", "tool"])
async def test_declared_timeout_grace_is_shared(
    store: ProgramStore, tmp_path: Path, entry: str
) -> None:
    async def handler(context: CallContext, arguments: dict[str, Any]) -> int:
        await asyncio.sleep(0.02)
        return 7

    executor = OperationExecutor(
        OperationRegistry([operation(handler, named=True, timeout=0.001, grace=0.1)]),
        CallContext("a", "member", "member"),
        store,
        default_timeout=0.001,
    )
    result = await invoke(entry, executor, tmp_path, {"timeout": 0.001})
    assert result == 7 if entry == "dsl" else result.success and result.data == 7


async def test_waiting_writer_timeout_is_definite_not_unknown(
    store: ProgramStore, tmp_path: Path
) -> None:
    calls = 0

    async def handler(context: CallContext, arguments: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        return 7

    executor = OperationExecutor(
        OperationRegistry([operation(handler)]),
        CallContext("a", "member", "member"),
        store,
        default_timeout=0.01,
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def owner() -> None:
        async with executor._writer.hold(True):
            entered.set()
            await release.wait()

    task = asyncio.create_task(owner())
    await entered.wait()
    try:
        with pytest.raises(ExecutionDeadlineError):
            await invoke("tool", executor, tmp_path)
        assert calls == 0
    finally:
        release.set()
        await task


@pytest.mark.parametrize("entry", ["dsl", "tool"])
async def test_domain_atomic_success_wins_over_timeout(
    store: ProgramStore, tmp_path: Path, entry: str
) -> None:
    async def handler(context: CallContext, arguments: dict[str, Any]) -> int:
        assert context.invocation is not None
        store.atomic_state_put(context.invocation, "committed", 7)
        await asyncio.sleep(1)
        return 7

    executor = OperationExecutor(
        OperationRegistry([operation(handler, safe=True)]),
        CallContext("a", "member", "member"),
        store,
        default_timeout=0.01,
    )
    result = await invoke(entry, executor, tmp_path)
    assert result == 7 if entry == "dsl" else result.success and result.data == 7
    assert store.state_get("committed") == 7


async def test_external_cancellation_propagates_without_revoking_receipt(
    store: ProgramStore,
) -> None:
    committed = asyncio.Event()

    async def handler(context: CallContext, arguments: dict[str, Any]) -> int:
        assert context.invocation is not None
        store.atomic_state_put(context.invocation, "committed", 7)
        committed.set()
        await asyncio.Event().wait()
        return 7

    executor = OperationExecutor(
        OperationRegistry([operation(handler, safe=True)]), CallContext("a", "root", "call"), store
    )
    task = asyncio.create_task(executor.call("slow-operation", {}))
    await committed.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await executor.call("slow-operation", {}) == 7


async def test_internal_model_observes_operation_deadline(store: ProgramStore) -> None:
    from tlon.base.execution import current_execution

    async def handler(context: CallContext, arguments: dict[str, Any]) -> int:
        scope = current_execution()
        assert scope is not None and scope.remaining_seconds() is not None
        assert scope.remaining_seconds() <= 0.1
        return 7

    executor = OperationExecutor(
        OperationRegistry([operation(handler, timeout=0.1)]),
        CallContext("a", "root", "call"),
        store,
    )
    with bind_execution(ExecutionScope(store, "root", 5, "output")):
        assert await executor.call("slow-operation", {}) == 7


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1, 0])
def test_invalid_timeout_metadata_is_rejected(value: Any) -> None:
    async def handler(context: CallContext, arguments: dict[str, Any]) -> int:
        return 7

    with pytest.raises(DefinitionError):
        operation(handler, timeout=value)


@pytest.mark.parametrize("existing_intent", [False, True])
async def test_delayed_begin_distinguishes_owned_fresh_intent_from_old_unknown(
    store: ProgramStore, monkeypatch: pytest.MonkeyPatch, existing_intent: bool
) -> None:
    import time

    calls = 0

    async def handler(context: CallContext, arguments: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        return 7

    executor = OperationExecutor(
        OperationRegistry([operation(handler)]),
        CallContext("a", "root", "call"),
        store,
        default_timeout=0.01,
    )
    invocation = executor._invocation(executor.registry.get("slow-operation"), {}, executor.context)
    if existing_intent:
        store.begin_invocation(invocation)
    original = store.begin_invocation

    def delayed(*args: Any) -> bool:
        time.sleep(0.03)
        return original(*args)

    monkeypatch.setattr(store, "begin_invocation", delayed)
    expected = UnknownOutcomeError if existing_intent else ExecutionDeadlineError
    with pytest.raises(expected):
        await executor.call("slow-operation", {})
    assert calls == 0
    receipt = store.lookup_invocation(invocation)
    if existing_intent:
        assert receipt is None
    else:
        assert receipt is not None and receipt.error_type == "ExecutionDeadlineError"


@pytest.mark.parametrize(
    "failure",
    [ExecutionDeadlineError, BudgetExceededError],
)
async def test_committed_success_wins_over_later_host_control_error(
    store: ProgramStore, failure: type[Exception]
) -> None:
    async def handler(context: CallContext, arguments: dict[str, Any]) -> int:
        assert context.invocation is not None
        store.atomic_state_put(context.invocation, "committed", 7)
        raise failure("downstream control check")

    executor = OperationExecutor(
        OperationRegistry([operation(handler, safe=True)]),
        CallContext("a", "root", "call"),
        store,
    )
    assert await executor.call("slow-operation", {}) == 7
    assert store.state_get("committed") == 7


async def test_no_store_dsl_and_tool_share_post_dispatch_timeout_classification() -> None:
    async def handler(context: CallContext, arguments: dict[str, Any]) -> int:
        await asyncio.sleep(1)
        return 7

    executor = OperationExecutor(
        OperationRegistry([operation(handler)]),
        CallContext("a", "temporary", "same-call"),
        default_timeout=0.01,
    )
    adapter = DSLAdapter(executor)
    with pytest.raises(UnknownOutcomeError) as failure:
        await Evaluator(adapter.builtins(), 1000, 100).eval_async(
            loads("(slow-operation {})"),
            {},
            0,
            adapter.handle,
        )
    with pytest.raises(UnknownOutcomeError) as tool_failure:
        await OperationTool(executor, "slow-operation").execute_context({})
    assert str(tool_failure.value) == str(failure.value)
    assert tool_failure.value.data == failure.value.data


async def test_no_store_waiting_writer_timeout_is_definite() -> None:
    calls = 0

    async def handler(context: CallContext, arguments: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        return 7

    executor = OperationExecutor(
        OperationRegistry([operation(handler)]),
        CallContext("a", "temporary", "call"),
        default_timeout=0.01,
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def owner() -> None:
        async with executor._writer.hold(True):
            entered.set()
            await release.wait()

    task = asyncio.create_task(owner())
    await entered.wait()
    try:
        adapter = DSLAdapter(executor)
        with pytest.raises(ExecutionDeadlineError) as failure:
            await Evaluator(adapter.builtins(), 1000, 100).eval_async(
                loads("(slow-operation {})"),
                {},
                0,
                adapter.handle,
            )
        with pytest.raises(ExecutionDeadlineError) as tool_failure:
            await OperationTool(executor, "slow-operation").execute_context({})
        assert str(tool_failure.value) == str(failure.value)
        assert calls == 0
    finally:
        release.set()
        await task


@pytest.mark.parametrize(
    "error",
    [
        UnknownOutcomeError("uncertain", {}),
        BudgetExceededError("budget"),
        ExecutionDeadlineError("expired"),
        RuntimeError("infrastructure"),
    ],
)
async def test_loop_driver_propagates_blocking_error_without_tool_result(
    tmp_path: Path,
    error: Exception,
) -> None:
    from unittest.mock import AsyncMock

    execution = OperationExecutor(
        OperationRegistry([operation(AsyncMock())]), CallContext("a", "r", "c")
    )
    loop = driver(tmp_path, execution)
    loop._tool_executor.execute = AsyncMock(side_effect=error)  # type: ignore[method-assign]
    loop._journal.append = AsyncMock()  # type: ignore[method-assign]
    with pytest.raises(type(error)) as failure:
        await loop.execute_one_tool("session", ToolCall("tool", "slow-operation", {}))
    assert failure.value is error
    loop._journal.append.assert_not_awaited()
