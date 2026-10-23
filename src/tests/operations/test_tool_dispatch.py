"""The program batch is composition; only indexed leaf effects are durable."""

from pathlib import Path
from typing import Any

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.base.execution import ExecutionScope, bind_execution
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.schema import (
    InputError,
    OutputError,
    RecoveryMismatchError,
    RejectedError,
    UnknownOutcomeError,
)
from tlon.operations.tool_dispatch import ToolDispatch

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}


def batch(*values: int) -> dict[str, Any]:
    return {
        "tools": ["write"],
        "tool_calls": [
            {"id": f"provider-{i}", "name": "write", "arguments": {"value": value}}
            for i, value in enumerate(values)
        ],
    }


@pytest.fixture
def store(tmp_path: Path) -> ProgramStore:
    return ProgramStore(tmp_path / "program.sqlite", build_id="test")


def setup_dispatch(
    store: ProgramStore, handler: Any, *, control: bool = False
) -> tuple[ToolDispatch, list[dict[str, Any]]]:
    op = Operation(
        "write", "test", SCHEMA, True, handler, effects=frozenset({"write"}), control=control
    )
    executor = OperationExecutor(
        OperationRegistry([op]), CallContext("member", "root", "parent"), store
    )
    catalog = [OperationTool(executor, "write").to_openai_definition()]
    store.freeze_model_context("root", "host", {"system_prompt": "test", "tools": catalog})
    return ToolDispatch(executor, lambda: catalog), catalog


async def test_partial_batch_replays_before_cancellation_and_revocation(
    store: ProgramStore,
) -> None:
    calls: list[int] = []

    async def write(context: CallContext, inputs: dict[str, Any]) -> Any:
        calls.append(inputs["value"])
        if inputs["value"] == 2:
            raise UnknownOutcomeError("uncertain", {})
        return inputs

    dispatch, catalog = setup_dispatch(store, write)
    context = CallContext("member", "root", "batch", ("3",))
    with (
        bind_execution(ExecutionScope(store, "root", 30, "output")),
        pytest.raises(UnknownOutcomeError),
    ):
        await dispatch.execute(batch(1, 2), context)
    catalog.clear()
    scope = ExecutionScope(store, "root", 30, "output")
    scope.cancel()
    with bind_execution(scope), pytest.raises(UnknownOutcomeError):
        await dispatch.execute(batch(1, 2), context)
    assert calls == [1, 2]
    assert store.get_effect("root", 3, "tool-execute", "{}")[0] is False


async def test_provider_ids_cannot_rewrite_partially_committed_batch(store: ProgramStore) -> None:
    async def write(context: CallContext, inputs: dict[str, Any]) -> Any:
        return inputs

    dispatch, _ = setup_dispatch(store, write)
    context = CallContext("member", "root", "batch", ("0",))
    with bind_execution(ExecutionScope(store, "root", 30, "output")):
        assert len(await dispatch.execute(batch(1), context)) == 1
    changed = batch(1)
    changed["tool_calls"][0]["id"] = "changed"
    with (
        bind_execution(ExecutionScope(store, "root", 30, "output")),
        pytest.raises(RecoveryMismatchError),
    ):
        await dispatch.execute(changed, context)


@pytest.mark.parametrize("kind", ["duplicate", "empty", "outside", "control"])
async def test_whole_batch_rejected_before_any_effect(store: ProgramStore, kind: str) -> None:
    calls: list[int] = []

    async def write(context: CallContext, inputs: dict[str, Any]) -> Any:
        calls.append(inputs["value"])
        return inputs

    dispatch, _ = setup_dispatch(store, write, control=kind == "control")
    arguments = batch(1, 2)
    if kind == "duplicate":
        arguments["tool_calls"][1]["id"] = "provider-0"
    elif kind == "empty":
        arguments["tool_calls"][1]["id"] = " "
    elif kind == "outside":
        arguments["tool_calls"][1]["name"] = "missing"
    with bind_execution(ExecutionScope(store, "root", 30, "output")), pytest.raises(InputError):
        await dispatch.execute(arguments, CallContext("member", "root", "batch"))
    assert calls == []


@pytest.mark.parametrize("error", [RejectedError("denied", {}), InputError("bad")])
async def test_domain_failures_keep_order_and_protocol_ids(
    store: ProgramStore, error: Exception
) -> None:
    async def write(context: CallContext, inputs: dict[str, Any]) -> Any:
        if inputs["value"] == 1:
            raise error
        return inputs

    dispatch, _ = setup_dispatch(store, write)
    with bind_execution(ExecutionScope(store, "root", 30, "output")):
        result = await dispatch.execute(batch(1, 2), CallContext("member", "root", "batch"))
    assert [item["id"] for item in result] == ["provider-0", "provider-1"]
    assert [item["success"] for item in result] == [False, True]


async def test_output_failure_propagates_instead_of_becoming_model_feedback(
    store: ProgramStore,
) -> None:
    async def write(context: CallContext, inputs: dict[str, Any]) -> Any:
        raise OutputError("invalid host result")

    dispatch, _ = setup_dispatch(store, write)
    with bind_execution(ExecutionScope(store, "root", 30, "output")), pytest.raises(OutputError):
        await dispatch.execute(batch(1), CallContext("member", "root", "batch"))


async def test_nested_program_dispatch_has_only_leaf_receipts_and_keeps_root_budget(
    store: ProgramStore,
) -> None:
    from unittest.mock import AsyncMock

    from tlon.agents.program_driver import ProgramDriver
    from tlon.agents.vocabulary import make_member_builtins
    from tlon.base.execution import current_execution
    from tlon.operations.dsl import DSLAdapter
    from tlosure.modules import ModulePackage

    calls: list[tuple[str, tuple[str, ...], str]] = []

    async def write(context: CallContext, inputs: dict[str, Any]) -> Any:
        scope = current_execution()
        assert scope is not None
        calls.append((scope.root_id, context.call_path, context.call_id))
        return inputs

    dispatch, _ = setup_dispatch(store, write)
    adapter = DSLAdapter(dispatch.executor, dispatch)
    builtins = {**make_member_builtins(), **adapter.builtins()}
    package = ModulePackage.from_sources(
        {
            "work.tlon": """(ns work (:export handle))
          (defn helper [value]
            (tool-execute {:tools ["write"] :tool_calls [{:id "from-model" :name "write" :arguments {:value value}}]}))
          (defn handle [event] (let [result (helper 7)] {:result result :continued true}))"""
        },
        builtins,
    )
    for _ in range(2):
        driver = ProgramDriver(
            store,
            "root",
            builtins,
            AsyncMock(),
            operations=adapter,
            member_id="member",
            token_budget=30,
        )
        result = await driver.run_entry(package.entry("work/handle"), {}, fuel=1000)
        assert result["continued"] is True
        assert result["result"][0]["data"] == {"value": 7}
    assert len(calls) == 1
    assert calls[0][0:2] == ("root", ("0", "tool", "0"))
    assert "from-model" not in calls[0][2]
    assert store.get_effect("root", 0, "tool-execute", "{}")[0] is False


async def test_invalid_changed_input_cannot_hide_previous_unknown(store: ProgramStore) -> None:
    async def write(context: CallContext, inputs: dict[str, Any]) -> Any:
        raise UnknownOutcomeError("uncertain", {})

    dispatch, _ = setup_dispatch(store, write)
    context = CallContext("member", "root", "batch")
    with (
        bind_execution(ExecutionScope(store, "root", 30, "output")),
        pytest.raises(UnknownOutcomeError),
    ):
        await dispatch.execute(batch(1), context)
    changed = batch(1)
    changed["tool_calls"][0]["arguments"]["value"] = "invalid"
    with (
        bind_execution(ExecutionScope(store, "root", 30, "output")),
        pytest.raises(RecoveryMismatchError),
    ):
        await dispatch.execute(changed, context)


async def test_mail_commit_failure_reports_actual_request_id_without_repeating_effect(
    store: ProgramStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.test_post_office import office_at
    from tlon.operations.mail import make_mail_operations
    from tlon.organization.mail.mailbox import Mailbox
    from tlon.organization.mail.member_mail import MemberMailService

    office = office_at(tmp_path)
    sender = Mailbox(office._paths.mail_database_file(), "a")
    receiver = Mailbox(office._paths.mail_database_file(), "b")
    executor = OperationExecutor(
        OperationRegistry(make_mail_operations(MemberMailService("a", sender, office))),
        CallContext("a", "root", "batch"),
        store,
    )
    catalog = [OperationTool(executor, "mail-send").to_openai_definition()]
    store.freeze_model_context("root", "host", {"system_prompt": "test", "tools": catalog})
    dispatch = ToolDispatch(executor, lambda: catalog)
    arguments = {
        "tools": ["mail-send"],
        "tool_calls": [
            {
                "id": "provider-mail",
                "name": "mail-send",
                "arguments": {
                    "to": ["b"],
                    "content": "send once",
                    "request_id": "explicit-mail-key",
                },
            }
        ],
    }
    original_commit = store.commit_invocation

    def fail_commit(*args: Any) -> None:
        raise OSError("operation receipt disk unavailable")

    monkeypatch.setattr(store, "commit_invocation", fail_commit)
    context = CallContext("a", "root", "batch", ("1",))
    with (
        bind_execution(ExecutionScope(store, "root", 30, "output")),
        pytest.raises(UnknownOutcomeError, match="could not be durably recorded") as failure,
    ):
        await dispatch.execute(arguments, context)
    assert failure.value.data == {
        "call_id": "batch:tool:0",
        "outcome": "unknown",
        "retry_safe": True,
        "request_id": "explicit-mail-key",
    }
    assert len(await receiver.inbox()) == 1
    monkeypatch.setattr(store, "commit_invocation", original_commit)
    with bind_execution(ExecutionScope(store, "root", 30, "output")):
        results = await dispatch.execute(arguments, context)
    assert results[0]["success"]
    assert results[0]["data"]["request_id"] == "explicit-mail-key"
    assert len(await receiver.inbox()) == 1


async def test_revoked_fresh_tool_blocks_before_model_feedback(store: ProgramStore) -> None:
    calls: list[int] = []

    async def write(context: CallContext, inputs: dict[str, Any]) -> Any:
        calls.append(inputs["value"])
        return inputs

    dispatch, catalog = setup_dispatch(store, write)
    original = list(catalog)
    catalog.clear()
    context = CallContext("member", "root", "batch", ("7",))
    with (
        bind_execution(ExecutionScope(store, "root", 30, "output")),
        pytest.raises(RecoveryMismatchError, match="no longer authorized"),
    ):
        await dispatch.execute(batch(1), context)
    assert calls == []
    # Revocation is a host blocker, never a tool failure appended to a later
    # model request. Restoring authority therefore cannot reinterpret a
    # previously reported rejection as a newly executed effect.
    catalog.extend(original)
    with bind_execution(ExecutionScope(store, "root", 30, "output")):
        assert (await dispatch.execute(batch(1), context))[0]["success"]
    assert calls == [1]
