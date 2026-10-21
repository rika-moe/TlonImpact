"""Compositions use the same schemas, budgets and durable leaf authority."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.operations.adapters import OperationTool
from tlon.operations.composite import CompositeSpec, compile_composites
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.schema import DefinitionError, OutputError, RecoveryMismatchError
from tlosure.builtins import ARITHMETIC_BUILTINS, CORE_BUILTINS, EvalDepthError, FuelExhaustedError
from tlosure.edn import Form, loads
from tlosure.eval import Evaluator

OBJECT: dict[str, Any] = {
    "type": "object",
    "properties": dict.fromkeys(("subject", "body", "x", "first", "second"), True),
    "required": [],
    "additionalProperties": False,
}
INTEGER = {"type": "integer"}


def spec(name: str, body: str, *, output: dict[str, Any] | bool = True) -> CompositeSpec:
    impl = loads(f"(fn [input] {body})")
    assert isinstance(impl, Form)
    return CompositeSpec(name, name, OBJECT, output, impl)


def executor(
    specs: list[CompositeSpec], hosts: tuple[Operation, ...] = (), store: ProgramStore | None = None
) -> OperationExecutor:
    catalog = compile_composites(specs, hosts)
    return OperationExecutor(
        OperationRegistry((*hosts, *catalog.operations)),
        CallContext("alice", "root", "outer"),
        store,
    )


async def test_two_composites_shared_dsl_tool_and_real_schema_boundaries() -> None:
    execution = executor(
        [
            spec(
                "summarize-thread",
                '(str (get input :subject) ": " (get input :body))',
                output={"type": "string"},
            ),
            spec("send-thread-summary", "(summarize-thread input)", output={"type": "string"}),
        ]
    )
    adapter = DSLAdapter(execution)
    value = Evaluator({**CORE_BUILTINS, **adapter.builtins()}, 100, 20).eval(
        loads('(send-thread-summary {:subject "hello" :body "world"})'), {}, 0
    )
    result = await OperationTool(execution, "send-thread-summary").execute_context(
        {"subject": "hello", "body": "world"}
    )
    assert value == result.data == "hello: world"
    assert result.success
    bad = executor([spec("bad-output", '"text"', output=INTEGER)])
    with pytest.raises(OutputError):
        bad.call_pure("bad-output", {})
    assert not (await OperationTool(bad, "bad-output").execute_context({})).success
    bad_data = executor([spec("bad-data", "(quote forbidden-symbol)")])
    with pytest.raises(OutputError, match="not JSON data"):
        bad_data.call_pure("bad-data", {})
    invalid_result = await OperationTool(bad_data, "bad-data").execute_context({})
    assert not invalid_result.success and invalid_result.error.startswith("OutputError:")



@pytest.mark.parametrize(
    "body",
    [
        "(missing-operation input)",
        "(get self :id)",
        "(input {})",
        "((if true (fn [x] x) (fn [x] x)) input)",
        "(let [f (get input :target)] (f {}))",
        "(fn [f] (f input))",
    ],
)
def test_closed_static_dependency_rejections(body: str) -> None:
    with pytest.raises(DefinitionError):
        compile_composites([spec("bad", body)], ())


def test_local_functions_all_branches_and_quote_are_scope_aware() -> None:
    async def read(_context: CallContext, _inputs: dict[str, Any]) -> int:
        return 4

    host = Operation("read-number", "", OBJECT, INTEGER, read, effects=frozenset({"read"}))
    composite = spec(
        "local-functions",
        "(let [f (fn [x] (if false (read-number x) 3))] (defn recurse [x] (if x (f input) (recurse true))) (recurse false))",
    )
    catalog = compile_composites([composite, spec("quoted", "(quote (read-number {}))")], [host])
    assert catalog.definitions["local-functions"].dependencies == ("read-number",)
    assert catalog.definitions["local-functions"].effects == frozenset({"read"})
    assert catalog.definitions["quoted"].dependencies == ()


def test_cycle_collision_and_invalid_closure_do_not_remove_independent_definitions() -> None:
    catalog = compile_composites(
        [
            spec("a", "(b input)"),
            spec("b", "(a input)"),
            spec("good", "3"),
            spec("broken", "(missing input)"),
            spec("parent", "(broken input)"),
        ],
        (),
        strict=False,
    )
    assert set(catalog.definitions) == {"good"}
    assert set(catalog.errors) == {"a", "b", "broken", "parent"}
    with pytest.raises(DefinitionError, match="collision"):
        compile_composites([spec("same", "3"), spec("same", "4")], ())


def test_control_flag_blocks_direct_and_hidden_wrapping_without_name_list() -> None:
    async def stop(_context: CallContext, _inputs: dict[str, Any]) -> None:
        return None

    control = Operation(
        "novel-control-name", "", OBJECT, True, stop, effects=frozenset({"write"}), control=True
    )
    with pytest.raises(DefinitionError, match="control"):
        compile_composites(
            [
                spec("leaf-wrapper", "(if false (novel-control-name {}) nil)"),
                spec("outer-wrapper", "(leaf-wrapper input)"),
            ],
            [control],
        )


def test_artifact_immutable_single_schema_and_no_legacy_format() -> None:
    source = spec("identity", "input", output=OBJECT)
    parsed = CompositeSpec.from_edn(source.to_edn())
    assert parsed.to_edn() == source.to_edn()
    parsed.input_schema["type"] = "string"
    parsed.impl.append(123)
    assert parsed.input_schema["type"] == "object"
    assert len(parsed.impl) == 3
    for old in [
        '{:version 1 :name "old" :args [] :pure true :impl (fn [] 3)}',
        '{:version 2 :name "old" :description "" :input-schema {} :output-schema true :impl (fn [a b] a)}',
    ]:
        with pytest.raises(DefinitionError):
            CompositeSpec.from_edn(old)


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_nested_composite_and_outer_dsl_share_fuel_and_depth(asynchronous: bool) -> None:
    definitions = [
        spec("leaf", "(+ (get input :x) 1)"),
        spec("middle", "(+ (leaf input) (leaf input))"),
        spec("outer", "(+ (middle input) (middle input))"),
    ]
    execution = executor(definitions)
    adapter = DSLAdapter(execution)
    expression = loads("(outer {:x 1})")
    vocabulary = {**CORE_BUILTINS, **ARITHMETIC_BUILTINS, **adapter.builtins()}
    with pytest.raises(FuelExhaustedError):
        evaluator = Evaluator(vocabulary, 18, 100)
        if asynchronous:
            await evaluator.eval_async(expression, {}, 0, adapter.handle)
        else:
            evaluator.eval(expression, {}, 0)
    with pytest.raises(EvalDepthError):
        evaluator = Evaluator(vocabulary, 500, 4)
        if asynchronous:
            await evaluator.eval_async(expression, {}, 0, adapter.handle)
        else:
            evaluator.eval(expression, {}, 0)


async def test_canceled_second_effect_replays_first_leaf_receipt_and_paths(tmp_path: Path) -> None:
    first_count, second_count = 0, 0
    started = asyncio.Event()
    complete = False

    async def first(_context: CallContext, _inputs: dict[str, Any]) -> dict[str, Any]:
        nonlocal first_count
        first_count += 1
        return {"first": first_count}

    async def second(_context: CallContext, _inputs: dict[str, Any]) -> dict[str, Any]:
        nonlocal second_count
        second_count += 1
        if not complete:
            started.set()
            await asyncio.Event().wait()
        return {"second": 2}

    hosts = (
        Operation(
            "first-write", "", OBJECT, OBJECT, first, effects=frozenset({"write"}), retry_safe=True
        ),
        Operation(
            "second-write",
            "",
            OBJECT,
            OBJECT,
            second,
            effects=frozenset({"write"}),
            retry_safe=True,
        ),
    )
    definitions = [
        spec("inner", "(second-write input)"),
        spec("flow", "(let [one (first-write input)] (inner one))"),
    ]
    database = tmp_path / "history.sqlite"
    store = ProgramStore(database, build_id="stable-host")
    execution = executor(definitions, hosts, store)
    task = asyncio.create_task(execution.call("flow", {}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    complete = True
    recovered_store = ProgramStore(database, build_id="stable-host")
    # Live capabilities are deleted entirely. Recovery uses persisted source.
    recovered = executor([], hosts, recovered_store)
    result = await recovered.call("flow", {})
    assert result == {"second": 2}
    assert first_count == 1 and second_count == 2
    assert recovered_store.load_definitions("root") is not None
    import sqlite3

    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT identity_json FROM invocations").fetchall()
    assert len(rows) == 4  # Two leaf receipts plus derived success caches, created after effects.
    assert "child:1:first-write" in rows[0][0]
    assert "child:2:inner" in rows[1][0] and "child:1:second-write" in rows[1][0]


def test_frozen_source_dependency_ids_and_selected_closure(tmp_path: Path) -> None:
    definitions = [spec("leaf", "1"), spec("parent", "(leaf input)"), spec("unrelated", "2")]
    execution = executor(definitions, store=ProgramStore(tmp_path / "db", build_id="host"))
    frozen = execution.for_root("root", ["parent"])
    assert set(frozen.store.load_definitions("root")) == {"leaf", "parent"}  # type: ignore[union-attr]
    assert frozen.call_pure("parent", {}) == 1
    updated = executor(
        [spec("leaf", "99"), spec("parent", "(leaf input)")],
        store=ProgramStore(tmp_path / "db", build_id="host"),
    )
    assert updated.call_pure("parent", {}) == 1
    assert updated.call_pure("parent", {}, replace(updated.context, run_id="new")) == 99
    with pytest.raises(RecoveryMismatchError):
        executor(
            definitions, store=ProgramStore(tmp_path / "db", build_id="different-host")
        ).for_root("root")


async def test_effectful_composite_nested_model_uses_one_root_allowance() -> None:
    from tlon.base.execution import BudgetExceededError, ExecutionScope

    requests = 0

    async def model(context: CallContext, _inputs: dict[str, Any]) -> int:
        nonlocal requests
        assert context.execution is not None

        async def execute(_limit: int) -> dict[str, Any]:
            nonlocal requests
            requests += 1
            return {"usage": {"completion_tokens": 4}, "value": 1}

        response = await context.execution.model_call({"messages": []}, 4, execute)
        return int(response["value"])

    host = Operation("sample-model", "", OBJECT, INTEGER, model, effects=frozenset({"read"}))
    definitions = [
        spec("nested-model", "(sample-model input)"),
        spec("model-flow", "(+ (nested-model input) (nested-model input))"),
    ]
    store = ProgramStore(None, build_id="budget")
    execution = executor(definitions, (host,), store)
    scope = ExecutionScope(store, "root", 4, "output")
    context = replace(execution.context, execution=scope)
    with pytest.raises(BudgetExceededError):
        await execution.call("model-flow", {}, context)
    assert requests == 1
    assert store.budget_usage("root")["used"] == 4
    # First nested model's real result replays; the second still cannot reset allowance.
    with pytest.raises(BudgetExceededError):
        await execution.call("model-flow", {}, context)
    assert requests == 1


async def test_effectful_composite_uses_callers_language_budget() -> None:
    async def read(_context: CallContext, _inputs: dict[str, Any]) -> int:
        return 1

    host = Operation("read-value", "", OBJECT, INTEGER, read, effects=frozenset({"read"}))
    execution = executor(
        [
            spec("reading", "(let [a (read-value input)] (+ a a a a a))"),
            spec("reading-twice", "(+ (reading input) (reading input))"),
        ],
        (host,),
    )
    adapter = DSLAdapter(execution)
    vocabulary = {**CORE_BUILTINS, **ARITHMETIC_BUILTINS, **adapter.builtins()}
    evaluator = Evaluator(vocabulary, 20, 100)
    with pytest.raises(FuelExhaustedError):
        await evaluator.eval_async(loads("(reading-twice {})"), {}, 0, adapter.handle)
    assert evaluator.fuel_remaining < 0


async def test_unsafe_unknown_leaf_is_not_blindly_retried_by_its_composite() -> None:
    from tlon.operations.schema import UnknownOutcomeError

    calls: list[str] = []
    started = asyncio.Event()

    async def leaf(_context: CallContext, inputs: dict[str, Any]) -> None:
        calls.append(str(inputs["subject"]))
        if inputs["subject"] == "second":
            started.set()
            await asyncio.Event().wait()

    host = Operation("unsafe-write", "", OBJECT, True, leaf, effects=frozenset({"write"}))
    flow = spec(
        "unsafe-flow",
        '(let [first (unsafe-write {:subject "first"})] (unsafe-write {:subject "second"}))',
    )
    execution = executor([flow], (host,), ProgramStore(None, build_id="unsafe"))
    task = asyncio.create_task(execution.call("unsafe-flow", {}))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(UnknownOutcomeError, match="automatic retry refused"):
        await execution.call("unsafe-flow", {})
    assert calls == ["first", "second"]


def test_frozen_transitive_host_contract_mismatch_rejects_before_execution(tmp_path: Path) -> None:
    async def read(_context: CallContext, _inputs: dict[str, Any]) -> int:
        raise AssertionError("must never dispatch")

    old = Operation("host-leaf", "", OBJECT, INTEGER, read, effects=frozenset({"read"}))
    definitions = [
        spec("leaf-wrapper", "(host-leaf input)"),
        spec("top-wrapper", "(leaf-wrapper input)"),
    ]
    database = tmp_path / "root"
    frozen = executor(definitions, (old,), ProgramStore(database, build_id="host")).for_root("root")
    assert frozen.registry.get("top-wrapper").composition is not None
    changed = Operation(
        "host-leaf", "", OBJECT, {"type": "number"}, read, effects=frozenset({"read"})
    )
    with pytest.raises(RecoveryMismatchError, match="dependencies changed"):
        executor([], (changed,), ProgramStore(database, build_id="host")).for_root("root")


@pytest.mark.parametrize("cancelled", [False, True])
async def test_composite_success_cache_replays_before_expired_or_cancelled_scope(
    cancelled: bool,
) -> None:
    import time

    from tlon.base.execution import ExecutionScope

    calls = 0

    async def write(_context: CallContext, _inputs: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        return 7

    host = Operation("cached-write", "", OBJECT, INTEGER, write, effects=frozenset({"write"}))
    store = ProgramStore(None, build_id="cache")
    execution = executor(
        [spec("cached-flow", "(+ (cached-write input) 1)", output=INTEGER)], (host,), store
    )
    scope = ExecutionScope(store, "root", 20, "output")
    context = replace(execution.context, execution=scope)
    assert await execution.call("cached-flow", {}, context) == 8
    if cancelled:
        scope.cancel()
    else:
        scope.deadline = time.time() - 1
    assert await execution.call("cached-flow", {}, context) == 8
    assert await execution.replay("cached-flow", {}, context) == (True, 8)
    result = await OperationTool(execution, "cached-flow").execute_context({}, context)
    assert result.success and result.data == 8
    assert calls == 1 and store.budget_usage("root")["used"] == 0


@pytest.mark.parametrize("persistent", [False, True])
async def test_composite_timeout_preserves_dispatched_child_unknown(persistent: bool) -> None:
    from tlon.operations.schema import UnknownOutcomeError

    dispatched = 0

    async def write(_context: CallContext, _inputs: dict[str, Any]) -> None:
        nonlocal dispatched
        dispatched += 1
        await asyncio.Event().wait()

    host = Operation("slow-write", "", OBJECT, True, write, effects=frozenset({"write"}))
    store = ProgramStore(None, build_id="timeout") if persistent else None
    execution = executor([spec("slow-flow", "(slow-write input)")], (host,), store)
    execution.default_timeout = 0.03
    with pytest.raises(UnknownOutcomeError) as outcome:
        await execution.call("slow-flow", {})
    assert outcome.value.data["outcome"] == "unknown"
    assert dispatched == 1


async def test_dangling_derived_parent_cache_intent_never_blocks_child_replay() -> None:
    from tlon.operations.invocation import Invocation, InvocationOutcome

    calls = 0

    class CrashStore(ProgramStore):
        crash = True

        def commit_invocation(self, invocation: Invocation, outcome: InvocationOutcome) -> None:
            if invocation.name == "cache-crash-flow" and self.crash:
                self.crash = False
                raise asyncio.CancelledError()
            super().commit_invocation(invocation, outcome)

    async def write(_context: CallContext, _inputs: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        return 9

    host = Operation("cache-crash-write", "", OBJECT, INTEGER, write, effects=frozenset({"write"}))
    store = CrashStore(None, build_id="cache-crash")
    execution = executor([spec("cache-crash-flow", "(cache-crash-write input)")], (host,), store)
    with pytest.raises(asyncio.CancelledError):
        await execution.call("cache-crash-flow", {})
    assert calls == 1
    assert await execution.call("cache-crash-flow", {}) == 9
    assert calls == 1


async def test_composite_timeout_before_writer_admission_is_definite() -> None:
    from tlon.base.execution import ExecutionDeadlineError

    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def write(_context: CallContext, _inputs: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return 1

    host = Operation(
        "held-write", "", OBJECT, INTEGER, write, effects=frozenset({"write"}), default_timeout=1
    )
    execution = executor([spec("wait-flow", "(held-write input)")], (host,))
    execution.default_timeout = 0.03
    holder = asyncio.create_task(execution.call("held-write", {}))
    await entered.wait()
    try:
        with pytest.raises(ExecutionDeadlineError):
            await execution.call("wait-flow", {})
        assert calls == 1
    finally:
        release.set()
        await holder


@pytest.mark.parametrize("cancelled", [False, True])
async def test_all_child_receipts_reconstruct_after_crash_before_parent_cache(
    tmp_path: Path,
    cancelled: bool,
) -> None:
    import time
    from unittest.mock import patch

    from tlon.base.execution import ExecutionScope

    calls = 0

    async def write(_context: CallContext, _inputs: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        return 7

    async def crash(_self: OperationExecutor, _invocation: Any, _outcome: Any) -> None:
        raise asyncio.CancelledError()

    host = Operation("finished-write", "", OBJECT, INTEGER, write, effects=frozenset({"write"}))
    database = tmp_path / "receipt-only.sqlite"
    store = ProgramStore(database, build_id="history-only")
    execution = executor([spec("finished-flow", "(+ (finished-write input) 1)")], (host,), store)
    with (
        patch.object(OperationExecutor, "_cache_composite", crash),
        pytest.raises(asyncio.CancelledError),
    ):
        await execution.call("finished-flow", {})
    recovered_store = ProgramStore(database, build_id="history-only")
    recovered = executor([], (host,), recovered_store)
    scope = ExecutionScope(recovered_store, "root", 20, "output")
    if cancelled:
        scope.cancel()
    else:
        scope.deadline = time.time() - 1
    context = replace(recovered.context, execution=scope)
    reconstructing = asyncio.Event()

    async def wait_before_cache(_self: OperationExecutor, _invocation: Any, _outcome: Any) -> None:
        reconstructing.set()
        await asyncio.Event().wait()

    # Root cancellation permits receipt-only reconstruction. A real task
    # cancellation must still interrupt that traversal at an await boundary.
    with patch.object(OperationExecutor, "_cache_composite", wait_before_cache):
        task = asyncio.create_task(recovered.call("finished-flow", {}, context))
        await reconstructing.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert await recovered.call("finished-flow", {}, context) == 8
    assert calls == 1
    assert await recovered.replay("finished-flow", {}, context) == (True, 8)


@pytest.mark.parametrize("cancelled", [False, True])
async def test_history_only_traversal_refuses_missing_child_dispatch(cancelled: bool) -> None:
    import time
    from unittest.mock import patch

    from tlon.base.execution import ExecutionDeadlineError, ExecutionScope

    calls: list[str] = []

    async def write(_context: CallContext, inputs: dict[str, Any]) -> int:
        calls.append(inputs["subject"])
        return 7

    host = Operation("history-write", "", OBJECT, INTEGER, write, effects=frozenset({"write"}))
    next_host = Operation("not-started", "", OBJECT, INTEGER, write, effects=frozenset({"write"}))
    store = ProgramStore(None, build_id="history-missing")
    execution = executor(
        [
            spec(
                "incomplete-flow",
                '(let [first (history-write {:subject "first"})] (not-started {:subject "second"}))',
            )
        ],
        (host, next_host),
        store,
    )
    original = OperationExecutor.call

    async def crash_before_dispatch(
        self: OperationExecutor, name: str, arguments: Any, context: CallContext | None = None
    ) -> Any:
        if name == "not-started":
            raise asyncio.CancelledError()
        return await original(self, name, arguments, context)

    with (
        patch.object(OperationExecutor, "call", crash_before_dispatch),
        pytest.raises(asyncio.CancelledError),
    ):
        await execution.call("incomplete-flow", {})
    scope = ExecutionScope(store, "root", 20, "output")
    if cancelled:
        scope.cancel()
    else:
        scope.deadline = time.time() - 1
    error_type = asyncio.CancelledError if cancelled else ExecutionDeadlineError
    with pytest.raises(error_type):
        await execution.call("incomplete-flow", {}, replace(execution.context, execution=scope))
    assert calls == ["first"]
