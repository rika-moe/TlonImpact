"""One durable operation boundary serves both drivers, including failure recovery."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.schema import (
    OutputError,
    RecoveryMismatchError,
    RejectedError,
    UnknownOutcomeError,
)


@pytest.fixture(params=[False, True], ids=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ProgramStore:
    return ProgramStore(tmp_path / "invocations.sqlite" if request.param else None, build_id="test")


def make_executor(
    store: ProgramStore, handler: Any, *, read: bool = False, retry_safe: bool = False
) -> OperationExecutor:
    operation = Operation(
        "sample",
        "sample",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        {"type": "integer"},
        handler,
        effects=frozenset({"read" if read else "write"}),
        retry_safe=retry_safe,
    )
    return OperationExecutor(
        OperationRegistry([operation]), CallContext("a", "root", "call"), store
    )


async def test_committed_read_and_write_never_reenter_changed_handler(store: ProgramStore) -> None:
    calls = 0

    async def handler(context: CallContext, args: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        assert context.invocation is not None
        return calls

    for read in (False, True):
        executor = make_executor(store, handler, read=read)
        context = CallContext("a", f"root:{read}", f"call:{read}")
        first = await executor.call("sample", {}, context)
        assert await executor.call("sample", {}, context) == first
    assert calls == 2


async def test_same_invocation_converges_across_executors(store: ProgramStore) -> None:
    calls = 0

    async def handler(context: CallContext, args: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return calls

    a, b = make_executor(store, handler), make_executor(store, handler)
    assert await asyncio.gather(a.call("sample", {}), b.call("sample", {})) == [1, 1]
    assert calls == 1


async def test_reversed_root_completion_preserves_identity(store: ProgramStore) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(context: CallContext, args: dict[str, Any]) -> int:
        if context.run_id == "slow":
            entered.set()
            await release.wait()
            return 1
        return 2

    executor = make_executor(store, handler, read=True)
    slow_context = CallContext("a", "slow", "slow:0", call_path=("0",))
    fast_context = CallContext("a", "fast", "fast:0", call_path=("0",))
    slow = asyncio.create_task(executor.call("sample", {}, slow_context))
    await entered.wait()
    assert await executor.call("sample", {}, fast_context) == 2
    release.set()
    assert await slow == 1
    assert await executor.call("sample", {}, slow_context) == 1


async def test_rejection_and_output_contract_failure_replay_without_effect(
    store: ProgramStore,
) -> None:
    calls = 0

    async def handler(context: CallContext, args: dict[str, Any]) -> Any:
        nonlocal calls
        calls += 1
        if context.run_id == "reject":
            raise RejectedError("denied", {"reason": "quota"})
        return {"durable_external_receipt": "abc"}

    executor = make_executor(store, handler)
    for root, kind in (("reject", RejectedError), ("output", OutputError)):
        context = CallContext("a", root, root)
        for _ in range(2):
            with pytest.raises(kind):
                await executor.call("sample", {}, context)
        invocation = executor._invocation(executor.registry.get("sample"), {}, context)
        outcome = store.lookup_invocation(invocation)
        assert outcome is not None
        if root == "output":
            assert outcome.raw_result == {"durable_external_receipt": "abc"}
    assert calls == 2


async def test_non_json_output_retains_only_diagnostic(store: ProgramStore) -> None:
    async def handler(context: CallContext, args: dict[str, Any]) -> Any:
        return object()

    executor = make_executor(store, handler)
    with pytest.raises(OutputError):
        await executor.call("sample", {})
    invocation = executor._invocation(executor.registry.get("sample"), {}, executor.context)
    outcome = store.lookup_invocation(invocation)
    assert outcome is not None and outcome.raw_result["type"] == "object"


async def test_unknown_unsafe_failure_replays_and_safe_failure_can_retry(
    store: ProgramStore,
) -> None:
    calls = 0

    async def handler(context: CallContext, args: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("response lost")
        return 2

    unsafe = make_executor(store, handler)
    for _ in range(2):
        with pytest.raises(UnknownOutcomeError, match="response lost"):
            await unsafe.call("sample", {})
    assert calls == 1
    calls = 0
    safe = make_executor(store, handler, retry_safe=True)
    context = CallContext("a", "safe", "safe")
    with pytest.raises(UnknownOutcomeError):
        await safe.call("sample", {}, context)
    assert await safe.call("sample", {}, context) == 2
    assert await safe.call("sample", {}, context) == 2
    assert calls == 2


async def test_cancelled_write_leaves_intent_and_refuses_retry(store: ProgramStore) -> None:
    calls = 0

    async def handler(context: CallContext, args: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError()

    executor = make_executor(store, handler)
    with pytest.raises(asyncio.CancelledError):
        await executor.call("sample", {})
    with pytest.raises(UnknownOutcomeError, match="automatic retry refused"):
        await executor.call("sample", {})
    assert calls == 1


async def test_failed_result_commit_is_unknown_and_does_not_repeat_effect(
    store: ProgramStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def handler(context: CallContext, args: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        return calls

    executor = make_executor(store, handler)
    original = store.commit_invocation

    def fail(*args: Any) -> None:
        raise OSError("disk failed")

    monkeypatch.setattr(store, "commit_invocation", fail)
    result = await OperationTool(executor, "sample").execute_context({})
    assert not result.success and result.data["call_id"] == "call"
    monkeypatch.setattr(store, "commit_invocation", original)
    with pytest.raises(UnknownOutcomeError, match="automatic retry refused"):
        await executor.call("sample", {})
    assert calls == 1


async def test_changed_definition_or_argument_identity_rejected(store: ProgramStore) -> None:
    async def handler(context: CallContext, args: dict[str, Any]) -> int:
        return 1

    executor = make_executor(store, handler)
    await executor.call("sample", {})
    with pytest.raises(RecoveryMismatchError):
        await executor.call(
            "sample", {}, replace(executor.context, arguments_json='{"changed":true}')
        )
    changed = make_executor(store, handler, read=True)
    with pytest.raises(RecoveryMismatchError):
        await changed.call("sample", {})


async def test_uncommitted_read_failure_can_resample(store: ProgramStore) -> None:
    calls = 0

    async def handler(context: CallContext, args: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("read response lost")
        return 2

    executor = make_executor(store, handler, read=True)
    with pytest.raises(UnknownOutcomeError):
        await executor.call("sample", {})
    assert await executor.call("sample", {}) == 2
    assert await executor.call("sample", {}) == 2
    assert calls == 2


async def test_domain_committed_receipt_wins_over_lost_return(store: ProgramStore) -> None:
    calls = 0

    async def handler(context: CallContext, args: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        assert context.invocation is not None
        store.atomic_state_put(context.invocation, "committed", 4)
        raise OSError("domain committed before return was lost")

    executor = make_executor(store, handler, retry_safe=True)
    assert await executor.call("sample", {}) == 4
    assert await executor.call("sample", {}) == 4
    assert store.state_get("committed") == 4
    assert calls == 1


async def test_store_wait_does_not_block_event_loop(
    store: ProgramStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    entered, release = threading.Event(), threading.Event()
    timed_out = False
    original = store.ensure_root

    def blocked(root: str) -> None:
        nonlocal timed_out
        entered.set()
        timed_out = not release.wait(timeout=2)
        original(root)

    async def handler(context: CallContext, args: dict[str, Any]) -> int:
        return 1

    monkeypatch.setattr(store, "ensure_root", blocked)
    executor = make_executor(store, handler)
    task = asyncio.create_task(executor.call("sample", {}))
    try:
        await asyncio.wait_for(asyncio.to_thread(entered.wait), timeout=1)
        # This turn remains runnable while the database worker is blocked.
        await asyncio.sleep(0)
        assert not timed_out
        assert not task.done()
    finally:
        release.set()
    assert await task == 1


async def test_domain_error_subclass_normalizes_identically_on_replay(store: ProgramStore) -> None:
    from tlon.operations.schema import OperationError

    class DomainError(OperationError):
        def __init__(self) -> None:
            super().__init__("device exited unsuccessfully")
            self.data = {"exit_code": 7, "stdout": "partial"}

    calls = 0

    async def handler(context: CallContext, args: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        raise DomainError()

    executor = make_executor(store, handler)
    tool = OperationTool(executor, "sample")
    first = await tool.execute_context({})
    replayed = await tool.execute_context({})
    assert first.to_dict() == replayed.to_dict()
    assert first.error == "OperationError: device exited unsuccessfully"
    assert first.data == {"exit_code": 7, "stdout": "partial"}
    assert calls == 1


async def test_explicit_gate_is_shared_by_later_executor_for_same_store(
    store: ProgramStore,
) -> None:
    from tlon.base.execution import MemberWriteGate

    first_entered, second_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    writes: list[str] = []

    async def handler(context: CallContext, args: dict[str, Any]) -> int:
        writes.append(context.call_id)
        if context.call_id == "first":
            first_entered.set()
            await release.wait()
        else:
            second_entered.set()
        return len(writes)

    operation = Operation(
        "sample",
        "sample",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        {"type": "integer"},
        handler,
        effects=frozenset({"write"}),
    )
    registry = OperationRegistry([operation])
    first = OperationExecutor(
        registry, CallContext("a", "root", "first"), store, write_gate=MemberWriteGate()
    )
    second = OperationExecutor(registry, CallContext("a", "root", "second"), store)
    a = asyncio.create_task(first.call("sample", {}))
    await first_entered.wait()
    b = asyncio.create_task(second.call("sample", {}))
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(second_entered.wait(), timeout=0.03)
        assert writes == ["first"]
    finally:
        release.set()
        await asyncio.gather(a, b)
    assert writes == ["first", "second"]


def test_store_cannot_silently_split_across_different_explicit_gates(store: ProgramStore) -> None:
    from tlon.base.execution import MemberWriteGate
    from tlon.operations.schema import DefinitionError

    registry = OperationRegistry([])
    gate = MemberWriteGate()
    OperationExecutor(registry, CallContext("a", "root", "one"), store, write_gate=gate)
    OperationExecutor(registry, CallContext("a", "root", "two"), store, write_gate=gate)
    with pytest.raises(DefinitionError, match="different member write gate"):
        OperationExecutor(
            registry, CallContext("a", "root", "three"), store, write_gate=MemberWriteGate()
        )
