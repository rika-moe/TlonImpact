"""Durable effects replay as values while unfinished work remains resumable."""

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from tlon.agents.program_driver import ProgramDriver
from tlon.agents.program_store import ProgramStore
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.execution import ExecutionDeadlineError, ExecutionScope, current_execution
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.model import model_operation_definitions
from tlon.operations.state import make_state_operations
from tlosure.builtins import EvalError
from tlosure.modules import ModulePackage


@pytest.fixture(params=[False, True], ids=["memory", "sqlite"])
def store(request: Any, tmp_path: Path) -> ProgramStore:
    return ProgramStore(
        tmp_path / "program.sqlite3" if request.param else None, build_id="test-build"
    )


def driver(
    store: ProgramStore,
    *,
    run_id: str = "run",
    model: Any = None,
    token_budget: int = 4096,
) -> ProgramDriver:
    definitions = list(make_state_operations(store))
    if model is not None:
        definitions.extend(
            Operation(
                op.name,
                op.description,
                op.input_schema,
                op.output_schema,
                model,
                effects=op.effects,
            )
            for op in model_operation_definitions()
        )
    adapter = DSLAdapter(
        OperationExecutor(
            OperationRegistry(definitions), CallContext("a", "member", "member"), store
        )
    )
    return ProgramDriver(
        store,
        run_id,
        {**make_member_builtins(), **adapter.builtins()},
        operations=adapter,
        member_id="a",
        token_budget=token_budget,
    )


async def run_program(running: ProgramDriver, source: str) -> Any:
    package = ModulePackage.from_sources(
        {"work.tlon": "(ns work (:export handle)) (defn handle [event] " + source + ")"},
        running.builtins,
    )
    return await running.run_entry(package.entry("work/handle"), {}, fuel=1000)


async def state_put(store: ProgramStore, call_id: str, key: str, value: Any) -> None:
    executor = OperationExecutor(
        OperationRegistry(make_state_operations(store)), CallContext("a", call_id, call_id), store
    )
    await executor.call("state-put", {"key": key, "value": value})


async def test_replay_returns_saved_results_without_repeating_effects(store: ProgramStore) -> None:
    await state_put(store, "setup", "text", "saved")
    program = '(let [text (state-get {:key "text"})] (state-put {:key "delivered" :value text}))'
    await run_program(driver(store), program)
    assert store.state_get("delivered") == "saved"
    await state_put(store, "change", "delivered", "new")
    await run_program(driver(store), program)
    assert store.state_get("delivered") == "new"


async def test_state_read_freezes_branch_when_current_state_changes(store: ProgramStore) -> None:
    await state_put(store, "setup", "enabled", True)
    attempts = 0
    output: list[str] = []

    async def execute(context: CallContext, args: dict[str, Any]) -> Any:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise asyncio.CancelledError()
        output.append(args["messages"][0]["content"])
        return {
            "content": "ok",
            "tool_calls": [],
            "finish_reason": "stop",
            "reasoning_content": None,
            "usage": {},
        }

    program = '(if (state-get {:key "enabled"}) (model-call {:messages [{:role "user" :content "original"}] :max_tokens 1}) (model-call {:messages [{:role "user" :content "changed"}] :max_tokens 1}))'
    # Simulate a crash after the read was committed but before model dispatch.
    adapter = driver(store).operations
    assert adapter is not None
    await adapter._executor.call(
        "state-get",
        {"key": "enabled"},
        CallContext("a", "run", "program:run:0:state-get", call_path=("0",), origin="program"),
    )
    await state_put(store, "change", "enabled", False)
    attempts = 1
    await run_program(driver(store, model=execute), program)
    assert output == ["original"]


async def test_token_budget_counts_recorded_calls_after_restart(store: ProgramStore) -> None:
    allowances: list[int] = []

    async def execute(context: CallContext, args: dict[str, Any]) -> Any:
        scope = current_execution()
        assert scope is not None

        async def complete(allowance: int) -> dict[str, Any]:
            allowances.append(allowance)
            return {
                "content": "answer",
                "tool_calls": [],
                "finish_reason": "stop",
                "reasoning_content": None,
                "usage": {},
            }

        return await scope.model_call(args, args["max_tokens"], complete)

    program = " ".join(
        '(model-call {:messages [{:role "user" :content "question"}] :max_tokens 4})'
        for _ in range(3)
    )
    for _ in range(2):
        with pytest.raises(EvalError, match="allowance exhausted"):
            await run_program(driver(store, model=execute, token_budget=5), program)
    assert allowances == [4, 1]
    assert store.budget_usage("run")["used"] == 5


async def test_unrecorded_model_dispatch_is_not_repeated(store: ProgramStore) -> None:
    attempts: list[str] = []

    async def fail(context: CallContext, args: dict[str, Any]) -> Any:
        attempts.append(context.call_id)
        raise RuntimeError("submission status unknown")

    program = '(model-call {:messages [{:role "user" :content "paper"}] :max_tokens 1})'
    from tlon.operations.schema import UnknownOutcomeError

    with pytest.raises(UnknownOutcomeError, match="submission status unknown"):
        await run_program(driver(store, model=fail), program)
    with pytest.raises(UnknownOutcomeError, match="submission status unknown"):
        await run_program(driver(store, model=fail), program)
    assert len(attempts) == 1


async def test_bad_builtin_arity_is_terminal_expression_error(store: ProgramStore) -> None:
    with pytest.raises(EvalError, match="exactly one"):
        await run_program(driver(store, run_id="bad-arity"), '(state-get "one" "extra")')


async def test_expired_program_scope_stops_before_new_state_write(store: ProgramStore) -> None:
    program = '(state-put {:key "after-deadline" :value true})'
    running = driver(store)
    running.execution_scope = ExecutionScope(store, "run", 4096, "output", deadline=time.time() - 1)
    with pytest.raises(EvalError) as failure:
        await run_program(running, program)
    assert isinstance(failure.value.__cause__, ExecutionDeadlineError)
    assert store.state_get("after-deadline") is None
