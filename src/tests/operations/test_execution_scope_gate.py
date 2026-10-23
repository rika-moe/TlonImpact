"""Member write admission preserves replay and separates task ownership from roots."""

import asyncio
import time
from dataclasses import replace
from typing import Any

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.base.execution import ExecutionDeadlineError, ExecutionScope
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.schema import OperationError


def make_executor(handler: Any) -> tuple[OperationExecutor, ProgramStore]:
    store = ProgramStore(None, build_id="scope-gate")
    schema = {
        "type": "object",
        "properties": {"label": {"type": "string"}},
        "required": ["label"],
        "additionalProperties": False,
    }
    operations = [
        Operation(name, name, schema, True, handler, effects=frozenset({effect}))
        for name, effect in (("write", "write"), ("read", "read"))
    ]
    return OperationExecutor(
        OperationRegistry(operations), CallContext("a", "root", "default"), store
    ), store


def context(store: ProgramStore, key: str, *, deadline: float | None = None) -> CallContext:
    return CallContext(
        "a", key, key, execution=ExecutionScope(store, key, 1000, "output", deadline=deadline)
    )


async def test_parallel_same_root_writes_serialize_while_read_passes() -> None:
    entered, release, read = asyncio.Event(), asyncio.Event(), asyncio.Event()
    active = 0
    peak = 0
    order: list[str] = []

    async def handler(ctx: CallContext, args: dict[str, Any]) -> str:
        nonlocal active, peak
        if args["label"] == "read":
            read.set()
            return "read"
        active += 1
        peak = max(peak, active)
        order.append(args["label"])
        if args["label"] == "first":
            entered.set()
            await release.wait()
        active -= 1
        return args["label"]

    executor, store = make_executor(handler)
    shared = ExecutionScope(store, "same-root", 1000, "output")
    first = asyncio.create_task(
        executor.call(
            "write", {"label": "first"}, CallContext("a", "same-root", "first", execution=shared)
        )
    )
    await entered.wait()
    second = asyncio.create_task(
        executor.call(
            "write", {"label": "second"}, CallContext("a", "same-root", "second", execution=shared)
        )
    )
    assert (
        await executor.call(
            "read", {"label": "read"}, CallContext("a", "same-root", "read", execution=shared)
        )
        == "read"
    )
    assert read.is_set() and order == ["first"]
    release.set()
    assert await asyncio.gather(first, second) == ["first", "second"]
    assert peak == 1


async def test_same_task_nested_write_reenters_but_same_identity_recursion_rejects() -> None:
    async def handler(ctx: CallContext, args: dict[str, Any]) -> str:
        if args["label"] == "outer":
            inner = replace(ctx, call_id="nested", invocation=None)
            return await executor.call("write", {"label": "inner"}, inner)
        if args["label"] == "recursive":
            with pytest.raises(OperationError, match="Recursive invocation"):
                await executor.call("write", args, ctx)
        return args["label"]

    executor, store = make_executor(handler)
    assert (
        await asyncio.wait_for(
            executor.call("write", {"label": "outer"}, context(store, "outer")), 1
        )
        == "inner"
    )
    assert (
        await asyncio.wait_for(
            executor.call("write", {"label": "recursive"}, context(store, "recursive")), 1
        )
        == "recursive"
    )


async def test_cancelled_scope_replays_history_but_never_dispatches_new_effect() -> None:
    calls: list[str] = []

    async def handler(ctx: CallContext, args: dict[str, Any]) -> str:
        calls.append(args["label"])
        return args["label"]

    executor, store = make_executor(handler)
    original = context(store, "cancel")
    assert await executor.call("write", {"label": "done"}, original) == "done"
    assert original.execution is not None
    original.execution.cancel()
    assert await executor.call("write", {"label": "done"}, original) == "done"
    fresh = replace(original, call_id="never-dispatched")
    with pytest.raises(asyncio.CancelledError):
        await executor.call("write", {"label": "fresh"}, fresh)
    assert calls == ["done"]
    invocation = executor._invocation(executor.registry.get("write"), {"label": "fresh"}, fresh)
    assert store.lookup_invocation(invocation) is None
    assert store.begin_invocation(invocation)


async def test_deadline_waiting_for_member_writer_never_creates_intent() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    calls: list[str] = []

    async def handler(ctx: CallContext, args: dict[str, Any]) -> str:
        calls.append(args["label"])
        entered.set()
        await release.wait()
        return args["label"]

    executor, store = make_executor(handler)
    first = asyncio.create_task(
        executor.call("write", {"label": "holder"}, context(store, "holder"))
    )
    await entered.wait()
    expired = context(store, "waiting", deadline=time.time() + 0.05)
    with pytest.raises(TimeoutError):
        await executor.call("write", {"label": "waiting"}, expired)
    assert calls == ["holder"]
    invocation = executor._invocation(executor.registry.get("write"), {"label": "waiting"}, expired)
    assert store.lookup_invocation(invocation) is None and store.begin_invocation(invocation)
    release.set()
    await first


async def test_expired_scope_replays_committed_receipt() -> None:
    async def handler(ctx: CallContext, args: dict[str, Any]) -> str:
        return args["label"]

    executor, store = make_executor(handler)
    original = context(store, "expired")
    assert await executor.call("write", {"label": "done"}, original) == "done"
    assert original.execution is not None
    original.execution.deadline = time.time() - 1
    assert await executor.call("write", {"label": "done"}, original) == "done"
    fresh = replace(original, run_id="expired-new", call_id="expired-new")
    fresh = replace(
        fresh,
        execution=ExecutionScope(store, fresh.run_id, 1000, "output", deadline=time.time() - 1),
    )
    with pytest.raises(ExecutionDeadlineError):
        await executor.call("write", {"label": "fresh"}, fresh)


async def test_nested_child_writer_cannot_deadlock_against_parallel_retry() -> None:
    outer_entered, child_waiting = asyncio.Event(), asyncio.Event()
    permit_nested = asyncio.Event()
    calls: list[str] = []

    async def handler(ctx: CallContext, args: dict[str, Any]) -> str:
        calls.append(args["label"])
        if args["label"] == "outer":
            outer_entered.set()
            await permit_nested.wait()
            return await executor.call("write", {"label": "child"}, child)
        return "child-result"

    executor, store = make_executor(handler)
    parallel = OperationExecutor(executor.registry, executor.context, store)
    child = context(store, "child")
    outer = asyncio.create_task(executor.call("write", {"label": "outer"}, context(store, "outer")))
    await outer_entered.wait()
    original_hold = parallel._writer.hold

    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def observe_wait(write: bool) -> AsyncIterator[None]:
        if asyncio.current_task() is retry:
            child_waiting.set()
        async with original_hold(write):
            yield

    parallel._writer.hold = observe_wait  # type: ignore[method-assign]
    retry = asyncio.create_task(parallel.call("write", {"label": "child"}, child))
    await child_waiting.wait()
    permit_nested.set()
    assert await asyncio.wait_for(asyncio.gather(outer, retry), 1) == [
        "child-result",
        "child-result",
    ]
    assert calls == ["outer", "child"]


async def test_deadline_after_write_dispatch_preserves_unknown_key_and_refuses_repeat() -> None:
    from tlon.operations.adapters import OperationTool
    from tlon.operations.schema import UnknownOutcomeError

    calls = 0

    async def handler(ctx: CallContext, args: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()
        return "unreachable"

    executor, store = make_executor(handler)
    ctx = context(store, "timed-write", deadline=time.time() + 0.05)
    with pytest.raises(UnknownOutcomeError) as failure:
        await OperationTool(executor, "write").execute_context({"label": "effect"}, ctx)
    assert failure.value.data == {
        "call_id": "timed-write",
        "outcome": "unknown",
        "retry_safe": False,
    }
    with pytest.raises(UnknownOutcomeError):
        await executor.call("write", {"label": "effect"}, ctx)
    assert calls == 1
