"""Module roots preserve failed evidence and never replay unsafe unknown calls."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.agents.test_module_programs import BEHAVIOR, _event, _member
from tlon.agents.member_runtime import MemberRuntime
from tlon.agents.program_package import validate_program_sources
from tlon.base.execution import UnknownModelOutcomeError
from tlon.operations.core import Operation, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.model import make_model_operations
from tlon.operations.schema import UnknownOutcomeError


def _runtime(home: Path, body: str, additions: tuple[Operation, ...] = ()) -> MemberRuntime:
    member = _member(home)
    original = member._operations._executor
    adapter = DSLAdapter(
        OperationExecutor(
            OperationRegistry([*original.registry.definitions(), *additions]),
            original.context,
            member._program_store,
        )
    )
    member._operations = adapter
    member._operation_executor = adapter._executor
    member._builtins.update(adapter.builtins())
    member._spec = validate_program_sources(
        BEHAVIOR,
        {"work/reply.tlon": "(ns work.reply (:export handle)) (defn handle [event] " + body + ")"},
        member._builtins,
    )
    return member


async def test_mismatched_module_root_preserves_input_and_allows_new_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = '(state-put {:key "effect" :value true}) {:result "done"}'
    before = _runtime(tmp_path, body)
    original = before._program_store.complete_entry

    def interrupt(*args: Any) -> Any:
        raise asyncio.CancelledError()

    monkeypatch.setattr(before._program_store, "complete_entry", interrupt)
    with pytest.raises(asyncio.CancelledError):
        await before._run_handler_body(before.spec.handlers[0], _event(), run_id="frozen")
    monkeypatch.setattr(before._program_store, "complete_entry", original)
    frozen = before._program_store.get_run("frozen")
    with before._program_store._connection() as connection:
        connection.execute("UPDATE roots SET build_id='previous-build' WHERE root_id='frozen'")
    restarted = _runtime(tmp_path, body)
    await restarted._restore_programs()
    blocked = restarted._program_store.get_run("frozen")
    assert frozen is not None and blocked is not None
    assert blocked.error and blocked.error.startswith("recovery_blocked:")
    assert not blocked.completed
    assert (blocked.package_edn, blocked.input_edn) == (frozen.package_edn, frozen.input_edn)
    assert await restarted._run_handler_body(restarted.spec.handlers[0], _event(), run_id="new")
    assert restarted._program_store.get_run("new").completed
    await restarted._restore_programs()
    assert restarted._program_store.get_run("frozen") == blocked


async def test_unknown_model_never_replays_provider_or_continuation(tmp_path: Path) -> None:
    provider = AsyncMock(side_effect=UnknownModelOutcomeError("response unknown"))
    operations = make_model_operations(
        type("Client", (), {"chat": provider})(), AsyncMock(return_value="persona")
    )
    member = _runtime(
        tmp_path,
        '(model-call {:messages [{:role "user" :content "work"}] :max_tokens 10}) '
        '(state-put {:key "after" :value true}) {:result nil}',
        operations,
    )
    with pytest.raises(UnknownOutcomeError):
        await member._run_handler_body(member.spec.handlers[0], _event(), run_id="unknown-model")
    provider.assert_awaited_once()
    blocked = member._program_store.get_run("unknown-model")
    assert blocked is not None and blocked.error and not blocked.completed
    assert blocked.error.startswith("recovery_blocked:")
    assert member._program_store.state_get("after") is None
    await member._restore_programs()
    await member._run_handler_body(member.spec.handlers[0], _event(), run_id="unknown-model")
    provider.assert_awaited_once()
    assert member._program_store.get_run("unknown-model") == blocked


async def test_unsafe_non_model_unknown_isolated_before_new_module_work(tmp_path: Path) -> None:
    unsafe = AsyncMock(
        side_effect=UnknownOutcomeError("submitted without receipt", {"retry_safe": False})
    )
    operation = Operation(
        "danger",
        "unsafe test",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        {"type": "null"},
        unsafe,
        effects=frozenset({"write"}),
    )
    member = _runtime(
        tmp_path, '(danger {}) (state-put {:key "after" :value true}) {:result nil}', (operation,)
    )
    with pytest.raises(UnknownOutcomeError):
        await member._run_handler_body(member.spec.handlers[0], _event(), run_id="unsafe")
    blocked = member._program_store.get_run("unsafe")
    assert blocked is not None and blocked.error and not blocked.completed
    assert member._program_store.state_get("after") is None
    await member._restore_programs()
    unsafe.assert_awaited_once()
    member._spec = _runtime(tmp_path, '(state-put {:key "healthy" :value true}) {:result nil}').spec
    await member._run_handler_body(member.spec.handlers[0], _event(), run_id="healthy")
    assert member._program_store.state_get("healthy") is True
    assert member._program_store.get_run("unsafe") == blocked
    unsafe.assert_awaited_once()
