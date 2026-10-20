"""Two state entry points share strict data and atomic durable replay."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tlon.agents.member_spec import MemberRule, MemberSpec
from tlon.agents.program_driver import ProgramDriver
from tlon.agents.program_store import ProgramStore
from tlon.agents.tool_executor import ToolExecutor
from tlon.operations.adapters import DSLAdapter, OperationTool
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.schema import (
    DefinitionError,
    InputError,
    OperationError,
    UnknownOutcomeError,
    admit_schema,
    edn_value,
    json_value,
)
from tlon.operations.state import make_state_operations
from tlosure.edn import Keyword, dumps, loads


def rule(source: str) -> MemberRule:
    return MemberSpec.from_edn(
        loads('{:version 1 :rules [{:name "test" :on :mail-delivered :actions [' + source + "]}]}")
    ).rules[0]


def executor(store: ProgramStore, member: str = "a") -> OperationExecutor:
    return OperationExecutor(
        OperationRegistry(make_state_operations(store)),
        CallContext(member, "session", "call-1"),
        store,
    )


async def test_tool_and_dsl_share_member_state_and_exact_key_spelling(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "state.sqlite")
    scope = executor(store)
    value = {"snake_key": [None, True, 1, 1.5, {"kebab-key": "ok"}]}
    result = await OperationTool(scope, "state-put").execute_context(
        {"key": "state", "value": value}
    )
    assert result.success and result.data == value
    adapter = DSLAdapter(scope)
    program = rule('(state-put {:key "copied" :value (state-get {:key "state"})})')
    await ProgramDriver(
        store, "program", adapter.builtins(), AsyncMock(), operations=adapter, member_id="a"
    ).run(program, {})
    assert (
        await scope.call("state-get", {"key": "copied"}, CallContext("a", "session", "read-copy"))
        == value
    )
    assert store.state_get("copied")[Keyword("snake_key")][-1] == {Keyword("kebab-key"): "ok"}
    other = executor(ProgramStore(tmp_path / "other.sqlite"), "b")
    assert await other.call("state-get", {"key": "state"}) is None


async def test_tool_retry_replays_original_receipt_without_overwriting_new_state(
    tmp_path: Path,
) -> None:
    store = ProgramStore(tmp_path / "state.sqlite")
    scope = executor(store)
    await scope.call("state-put", {"key": "x", "value": 1})
    await scope.call("state-put", {"key": "x", "value": 2}, CallContext("a", "session", "call-2"))
    assert (
        await executor(ProgramStore(store.database)).call("state-put", {"key": "x", "value": 1})
        == 1
    )
    assert store.state_get("x") == 2
    with pytest.raises(OperationError, match="changed"):
        await scope.call("state-put", {"key": "x", "value": 3})


async def test_program_transaction_failure_rolls_back_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProgramStore(tmp_path / "state.sqlite")
    scope = executor(store)
    context = CallContext("a", "run", "program:run:0:state-put", call_path=("0",))
    original = store._commit

    def fail(*args: object) -> None:
        if args[1].success:
            raise OSError("record failure")
        original(*args)

    monkeypatch.setattr(store, "_commit", fail)
    with pytest.raises(UnknownOutcomeError, match="record failure"):
        await scope.call("state-put", {"key": "x", "value": 1}, context)
    restarted = ProgramStore(store.database)
    assert restarted.state_get("x") is None
    assert not restarted._effects
    monkeypatch.setattr(store, "_commit", original)
    assert await scope.call("state-put", {"key": "x", "value": 1}, context) == 1
    assert restarted.state_get("x") == 1


async def test_post_commit_response_loss_does_not_rewrite_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProgramStore(tmp_path / "state.sqlite")
    scope = executor(store)
    context = CallContext(
        "a",
        "run",
        "program:run:0:state-put",
        call_path=("0",),
    )
    original = store.atomic_state_put

    def lost(*args: object) -> object:
        original(*args)
        raise OSError("response lost")

    monkeypatch.setattr(store, "atomic_state_put", lost)
    assert await scope.call("state-put", {"key": "x", "value": {"snake_key": True}}, context) == {
        "snake_key": True
    }
    restarted = ProgramStore(store.database)
    await executor(restarted).call(
        "state-put", {"key": "x", "value": 9}, CallContext("a", "session", "later")
    )
    assert await executor(restarted).call(
        "state-put", {"key": "x", "value": {"snake_key": True}}, context
    ) == {"snake_key": True}
    assert restarted.state_get("x") == 9


@pytest.mark.parametrize(
    "arguments",
    [
        {"key": "x", "value": 1, "member_id": "b"},
        {"key": "x", "value": 1, "effect_index": 0},
        {"key": "", "value": 1},
        {"key": "x", "value": float("inf")},
    ],
)
async def test_input_cannot_inject_context_or_non_json(arguments: dict[str, object]) -> None:
    store = ProgramStore(None)
    with pytest.raises(InputError):
        await executor(store).call("state-put", arguments)
    assert not store._effects and not store._invocations and not store._state


def test_boolean_schema_is_explicit_and_input_root_stays_closed() -> None:
    assert admit_schema(True) is True
    assert admit_schema(False) is False
    for schema in [
        True,
        False,
        {},
        {"type": "object", "properties": {}, "required": [], "additionalProperties": True},
    ]:
        with pytest.raises(DefinitionError):
            admit_schema(schema, input_schema=True)
    admitted = admit_schema({"type": "array", "items": True})
    assert admitted["items"] is True
    with pytest.raises(DefinitionError):
        admit_schema({"type": "array", "items": {}})


async def test_invalid_program_input_does_not_claim_intent() -> None:
    store = ProgramStore(None)
    adapter = DSLAdapter(executor(store))
    program = rule('(state-put {:key "x" :value 1 :effect_index 0})')
    with pytest.raises(InputError):
        await ProgramDriver(
            store, "program", adapter.builtins(), AsyncMock(), operations=adapter, member_id="a"
        ).run(program, {})
    assert not store._effects and not store._invocations and not store._state


async def test_false_schema_rejects_even_json_values() -> None:
    from tlon.operations.schema import OutputError

    operation = Operation(
        "deny",
        "deny all outputs",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        False,
        lambda _context, _args: None,
    )
    with pytest.raises(OutputError):
        await OperationExecutor(OperationRegistry([operation]), CallContext("a", "r", "c")).call(
            "deny", {}
        )


@pytest.mark.parametrize(
    "key,is_keyword",
    [
        ("a@tlon.internal", False),
        ("", False),
        ("two words", False),
        ("中文", False),
        ("snake_key", True),
        ("hyphen-key", True),
        ("x;comment", False),
    ],
)
def test_json_object_keys_roundtrip_through_edn(key: str, is_keyword: bool) -> None:
    original = {key: {"nested_key": [{key: True}]}}
    projected = edn_value(original)
    assert (type(next(iter(projected))) is Keyword) is is_keyword
    assert json_value(loads(dumps(projected)), edn_keys=True) == original


async def test_dynamic_json_keys_survive_sqlite_state_and_program_replay(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "dynamic.sqlite")
    original = {"a@tlon.internal": {"": 1, "中文": "ok", "two words": True}}
    await executor(store).call("state-put", {"key": "delivered", "value": original})
    restarted = ProgramStore(store.database)
    assert (
        await executor(restarted).call(
            "state-get", {"key": "delivered"}, CallContext("a", "session", "read-original")
        )
        == original
    )
    adapter = DSLAdapter(executor(restarted))
    program = rule('(state-put {:key "copy" :value (state-get {:key "delivered"})})')
    await ProgramDriver(
        restarted, "program", adapter.builtins(), AsyncMock(), operations=adapter, member_id="a"
    ).run(program, {})
    assert (
        await executor(restarted).call(
            "state-get", {"key": "copy"}, CallContext("a", "session", "read-copy")
        )
        == original
    )


async def test_tool_executor_recovers_committed_receipt_and_safe_same_call_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProgramStore(tmp_path / "tool.sqlite")
    scope = executor(store)
    tools = ToolExecutor()
    tools.register_tool(OperationTool(scope, "state-put"))
    original = store.atomic_state_put

    def lost(*args: object) -> object:
        original(*args)
        raise OSError("response lost after commit")

    monkeypatch.setattr(store, "atomic_state_put", lost)
    context = CallContext("a", "session", "session:s:call-1")
    arguments = {"key": "x", "value": 1}
    result = await tools.execute("state-put", arguments, context)
    assert result.success and result.data == 1
    restarted = ProgramStore(store.database)
    await executor(restarted).call(
        "state-put", {"key": "x", "value": 2}, CallContext("a", "session", "new")
    )
    replay_tools = ToolExecutor()
    replay_tools.register_tool(OperationTool(executor(restarted), "state-put"))
    replay = await replay_tools.execute("state-put", arguments, context)
    assert replay.success and replay.data == 1
    assert restarted.state_get("x") == 2
    conflict = await replay_tools.execute("state-put", {"key": "x", "value": 3}, context)
    assert not conflict.success and not conflict.data
    assert "RecoveryMismatchError" in conflict.error and "unknown" not in conflict.error


async def test_value_error_after_commit_recovers_authoritative_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProgramStore(tmp_path / "value-error.sqlite")
    original = store.atomic_state_put

    def lost(*args: object) -> object:
        original(*args)
        raise ValueError("Program replay effect name/arguments changed")

    monkeypatch.setattr(store, "atomic_state_put", lost)
    assert await executor(store).call("state-put", {"key": "x", "value": 1}) == 1
    assert ProgramStore(store.database).state_get("x") == 1
