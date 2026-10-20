"""Durable effects replay as values while unfinished work remains resumable."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tlon.agents.member_spec import MemberRule, MemberSpec
from tlon.agents.program_driver import ProgramDriver
from tlon.agents.program_store import ProgramStore
from tlon.agents.vocabulary import make_member_builtins
from tlon.operations.adapters import DSLAdapter
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.state import make_state_operations
from tlosure.builtins import ActionRequest, EvalError
from tlosure.edn import Keyword, dumps, loads


def rule(source: str) -> MemberRule:
    return MemberSpec.from_edn(
        loads('{:version 1 :rules [{:name "test" :on :mail-delivered :actions [' + source + "]}]}")
    ).rules[0]


@pytest.fixture(params=[False, True], ids=["memory", "sqlite"])
def store(request: Any, tmp_path: Path) -> ProgramStore:
    return ProgramStore(tmp_path / "program.sqlite3" if request.param else None)


def driver(store: ProgramStore, execute: Any, *, run_id: str = "run") -> ProgramDriver:
    adapter = DSLAdapter(
        OperationExecutor(
            OperationRegistry(make_state_operations(store)), CallContext("a", "member", "member")
        )
    )
    return ProgramDriver(
        store,
        run_id,
        {**make_member_builtins(), **adapter.builtins()},
        execute,
        operations=adapter,
        member_id="a",
    )


async def test_replay_returns_saved_results_without_repeating_effects(store: ProgramStore) -> None:
    store.atomic_state_put_record("setup", 0, "state-put", "[]", "text", "saved")
    program = rule(
        '(let [text (state-get {:key "text"})] (state-put {:key "delivered" :value text}))'
    )
    forbidden = AsyncMock(side_effect=AssertionError("state operations bypass legacy dispatcher"))
    await driver(store, forbidden).run(program, {})
    assert store.state_get("delivered") == "saved"
    store.atomic_state_put_record("change", 0, "state-put", "[]", "delivered", "new")
    await driver(store, forbidden).run(program, {})
    assert store.state_get("delivered") == "new"
    forbidden.assert_not_awaited()


async def test_state_read_freezes_branch_when_current_state_changes(store: ProgramStore) -> None:
    store.atomic_state_put_record("setup", 0, "state-put", "[]", "enabled", True)
    attempts = 0
    output: list[str] = []

    async def execute(request: ActionRequest, name: str, index: int, args: str) -> Any:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise asyncio.CancelledError()
        output.append(request.data["prompt"])
        return None

    program = rule(
        '(if (state-get {:key "enabled"}) (llm-call {:prompt "original" :max-tokens 1}) (llm-call {:prompt "changed" :max-tokens 1}))'
    )
    with pytest.raises(asyncio.CancelledError):
        await driver(store, execute).run(program, {})
    store.atomic_state_put_record("change", 0, "state-put", "[]", "enabled", False)
    await driver(store, execute).run(program, {})
    assert output == ["original"]


async def test_token_budget_counts_recorded_calls_after_restart(store: ProgramStore) -> None:
    allowances: list[int] = []

    async def execute(request: ActionRequest, name: str, index: int, args: str) -> Any:
        allowances.append(request.data["max_tokens"])
        return {Keyword("content"): "answer"}

    program = rule(" ".join('(llm-call {:prompt "question" :max-tokens 4})' for _ in range(3)))
    for _ in range(2):
        with pytest.raises(EvalError, match="budget exhausted"):
            await ProgramDriver(store, "run", make_member_builtins(), execute, token_budget=5).run(
                program, {}
            )
    assert allowances == [4, 1]


async def test_unrecorded_failure_retries_same_effect_index(store: ProgramStore) -> None:
    attempts: list[int] = []

    async def fail(request: ActionRequest, name: str, index: int, args: str) -> Any:
        attempts.append(index)
        raise OSError("submission status unknown")

    program = rule('(llm-call {:prompt "paper" :max-tokens 1})')
    for _ in range(2):
        with pytest.raises(OSError, match="submission status unknown"):
            await ProgramDriver(store, "run", make_member_builtins(), fail).run(program, {})
    assert attempts == [0, 0]
    assert store.get_effect(
        "run", 0, "llm-call", dumps([{Keyword("prompt"): "paper", Keyword("max-tokens"): 1}])
    ) == (False, None)


async def test_bad_builtin_arity_is_terminal_expression_error(store: ProgramStore) -> None:
    execute = AsyncMock()
    with pytest.raises(EvalError, match="exactly one"):
        await driver(store, execute, run_id="bad-arity").run(rule('(state-get "one" "extra")'), {})
    execute.assert_not_awaited()


async def test_unknown_host_runtime_error_retains_original_type(store: ProgramStore) -> None:
    execute = AsyncMock(side_effect=RuntimeError("response lost after commit"))
    with pytest.raises(RuntimeError, match="response lost"):
        await ProgramDriver(store, "unknown", make_member_builtins(), execute).run(
            rule('(llm-call {:prompt "paper" :max-tokens 1})'), {}
        )
