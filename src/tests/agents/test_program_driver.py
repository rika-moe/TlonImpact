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
from tlosure.builtins import ActionRequest, EvalError
from tlosure.edn import Keyword, dumps, loads


def rule(source: str) -> MemberRule:
    return MemberSpec.from_edn(
        loads('{:version 1 :rules [{:name "test" :on :mail-delivered :actions [' + source + "]}]}")
    ).rules[0]


@pytest.fixture(params=[False, True], ids=["memory", "sqlite"])
def store(request: Any, tmp_path: Path) -> ProgramStore:
    return ProgramStore(tmp_path / "program.sqlite3" if request.param else None)


async def test_replay_returns_saved_results_without_repeating_effects(store: ProgramStore) -> None:
    output: list[str] = []

    async def execute(request: ActionRequest, name: str, index: int, args: str) -> Any:
        if request.kind == "state_get":
            return "saved"
        output.append(request.data["value"])
        return {Keyword("status"): "accepted"}

    program = rule('(let [text (state-get "text")] (state-put "delivered" text))')
    driver = ProgramDriver(store, "run", make_member_builtins(), execute)
    await driver.run(program, {})
    assert output == ["saved"]
    forbidden = AsyncMock(side_effect=AssertionError("replayed effects must not run"))
    await ProgramDriver(store, "run", make_member_builtins(), forbidden).run(program, {})
    forbidden.assert_not_awaited()


async def test_state_read_freezes_branch_when_current_state_changes(store: ProgramStore) -> None:
    state = {"enabled": True}
    attempts = 0
    output: list[str] = []

    async def execute(request: ActionRequest, name: str, index: int, args: str) -> Any:
        nonlocal attempts
        if request.kind == "state_get":
            return state["enabled"]
        attempts += 1
        if attempts == 1:
            raise asyncio.CancelledError()
        output.append(request.data["value"])
        return None

    program = rule(
        '(if (state-get "enabled") (state-put "delivered" "original") (state-put "delivered" "changed"))'
    )
    with pytest.raises(asyncio.CancelledError):
        await ProgramDriver(store, "run", make_member_builtins(), execute).run(program, {})
    state["enabled"] = False
    await ProgramDriver(store, "run", make_member_builtins(), execute).run(program, {})
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

    program = rule('(state-put "edition:a" "paper")')
    for _ in range(2):
        with pytest.raises(OSError, match="submission status unknown"):
            await ProgramDriver(store, "run", make_member_builtins(), fail).run(program, {})
    assert attempts == [0, 0]
    assert store.get_effect(
        "run",
        0,
        "state-put",
        dumps(["edition:a", "paper"]),
    ) == (False, None)


async def test_bad_builtin_arity_is_terminal_expression_error(store: ProgramStore) -> None:
    execute = AsyncMock()
    with pytest.raises(EvalError, match="invalid arguments"):
        await ProgramDriver(store, "bad-arity", make_member_builtins(), execute).run(
            rule('(state-get "one" "extra")'), {}
        )
    execute.assert_not_awaited()


async def test_unknown_host_runtime_error_retains_original_type(store: ProgramStore) -> None:
    execute = AsyncMock(side_effect=RuntimeError("response lost after commit"))
    with pytest.raises(RuntimeError, match="response lost"):
        await ProgramDriver(store, "unknown", make_member_builtins(), execute).run(
            rule('(state-put "stable" "paper")'), {}
        )
    assert store.get_effect(
        "unknown",
        0,
        "state-put",
        dumps(["stable", "paper"]),
    ) == (False, None)
