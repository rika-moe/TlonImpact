"""Program result reads are member-bound and frozen as operation receipts."""

from pathlib import Path

import pytest

from tests.agents.test_previous_entry import entry
from tlon.agents.program_store import ProgramStore
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.definitions import host_operation_definitions
from tlon.operations.runs import make_run_operations
from tlon.operations.schema import InputError, OperationError


@pytest.mark.parametrize("disk", [False, True])
async def test_read_is_frozen_on_replay(tmp_path: Path, disk: bool) -> None:
    path = tmp_path / "a.sqlite3" if disk else None
    store = ProgramStore(path, build_id="test")
    entry(store, "old")
    store.complete_entry("old", {"messages": ["saved"]})
    entry(store, "current")
    executor = OperationExecutor(
        OperationRegistry(make_run_operations(store, "a")),
        CallContext("a", "current", "read"),
        store,
    )
    value = await executor.call("run-previous", {})
    assert value["previous"]["result"] == {"messages": ["saved"]}
    assert "run-previous" in {operation.name for operation in host_operation_definitions()}
    if path is not None:
        store = ProgramStore(path, build_id="test")

    # Removing the query implementation proves recovery uses its receipt, not a new read.
    def unavailable(run_id: str) -> dict[str, object]:
        raise AssertionError("must replay saved result")

    store.previous_entry = unavailable  # type: ignore[method-assign]
    restored = OperationExecutor(
        OperationRegistry(make_run_operations(store, "a")),
        CallContext("a", "current", "read"),
        store,
    )
    assert await restored.call("run-previous", {}) == value


async def test_member_isolation_and_no_external_selection() -> None:
    store = ProgramStore(None, build_id="test")
    entry(store, "current")
    registry = OperationRegistry(make_run_operations(store, "a"))
    wrong = OperationExecutor(registry, CallContext("b", "current", "read"))
    with pytest.raises(OperationError, match="another member"):
        await wrong.call("run-previous", {})
    owner = OperationExecutor(registry, CallContext("a", "current", "read"))
    with pytest.raises(InputError):
        await owner.call("run-previous", {"run_id": "other"})
    unbound = OperationExecutor(
        OperationRegistry(make_run_operations()), CallContext("a", "current", "read")
    )
    with pytest.raises(OperationError, match="not bound"):
        await unbound.call("run-previous", {})
