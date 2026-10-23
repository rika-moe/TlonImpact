"""The sole leaf-call authority survives crashes without changing frozen identity."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.operations.invocation import Invocation, InvocationOutcome
from tlon.operations.schema import RecoveryMismatchError


def invocation(**changes: object) -> Invocation:
    return replace(
        Invocation(
            "call", "root", ("0",), "state-put", '{"key":"x","value":1}', "definition", "build"
        ),
        **changes,
    )


@pytest.mark.parametrize("disk", [False, True])
def test_identity_and_outcomes_are_frozen_and_copied(tmp_path: Path, disk: bool) -> None:
    store = ProgramStore(tmp_path / "store.sqlite" if disk else None, build_id="build")
    call = invocation()
    assert store.lookup_invocation(call) is None
    assert store.begin_invocation(call)
    assert not store.begin_invocation(call)
    for change in [
        {"name": "other"},
        {"arguments_json": "{}"},
        {"definition_id": "other"},
        {"build_id": "other"},
        {"path": ("1",)},
        {"root_id": "other"},
        {"call_id": "other"},
    ]:
        with pytest.raises(RecoveryMismatchError):
            store.lookup_invocation(invocation(**change))
    data = {"nested": [1]}
    store.commit_invocation(call, InvocationOutcome(True, value=data))
    data["nested"].append(2)
    result = store.lookup_invocation(call)
    assert result is not None and result.value == {"nested": [1]}
    result.value["nested"].append(3)
    assert store.lookup_invocation(call).value == {"nested": [1]}
    with pytest.raises(RecoveryMismatchError):
        store.commit_invocation(call, InvocationOutcome(True, value=2))


@pytest.mark.parametrize("disk", [False, True])
def test_unknown_can_be_resolved_but_success_cannot_change(tmp_path: Path, disk: bool) -> None:
    store = ProgramStore(tmp_path / "store.sqlite" if disk else None, build_id="build")
    call = invocation()
    store.begin_invocation(call)
    unknown = InvocationOutcome(
        False, error_type="UnknownOutcomeError", error_data={"outcome": "unknown"}
    )
    store.commit_invocation(call, unknown)
    store.commit_invocation(call, InvocationOutcome(True, value=1))
    with pytest.raises(RecoveryMismatchError):
        store.commit_invocation(call, unknown)


def test_restart_build_drift_rejects_program_and_session_roots(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite"
    store = ProgramStore(path, build_id="build")
    store.ensure_entry("program", "source", "input", {})
    store.ensure_root("session")
    changed = ProgramStore(path, build_id="changed")
    # Enumeration must expose every root so the runtime can isolate each one.
    assert [run.run_id for run in changed.pending()] == ["program"]
    for action in [
        lambda: changed.ensure_entry("program", "source", "input", {}),
        lambda: changed.ensure_root("program"),
        lambda: changed.ensure_root("session"),
    ]:
        with pytest.raises(RecoveryMismatchError):
            action()


def test_concurrent_roots_initialize_without_partial_schema(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite"

    def register(index: int) -> None:
        ProgramStore(path, build_id="build").ensure_root(str(index))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(register, range(16)))
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM roots").fetchone()[0] == 16


def test_old_database_is_rejected_without_destroying_history(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value_edn TEXT)")
        connection.execute("INSERT INTO state VALUES ('kept','123')")
    with pytest.raises(RecoveryMismatchError, match="format"):
        ProgramStore(path, build_id="build").state_get("kept")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value_edn FROM state").fetchone()[0] == "123"
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall() == [("state",)]


@pytest.mark.parametrize("disk", [False, True])
def test_state_receipt_failure_rolls_back_both_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, disk: bool
) -> None:
    store = ProgramStore(tmp_path / "store.sqlite" if disk else None, build_id="build")
    call = invocation()
    original = store._commit

    def fail_after_record(*args: object) -> None:
        original(*args)
        raise OSError("receipt write failed")

    monkeypatch.setattr(store, "_commit", fail_after_record)
    with pytest.raises(OSError):
        store.atomic_state_put(call, "x", 1)
    assert store.state_get("x") is None
    assert store.lookup_invocation(call) is None


@pytest.mark.parametrize("disk", [False, True])
def test_contract_failure_retains_original_receipt(tmp_path: Path, disk: bool) -> None:
    store = ProgramStore(tmp_path / "store.sqlite" if disk else None, build_id="build")
    call = invocation()
    outcome = InvocationOutcome(
        False,
        error_type="OutputError",
        error_message="bad output",
        error_data={"kind": "contract"},
        raw_result={"receipt": "committed"},
    )
    store.commit_invocation(call, outcome)
    restarted = ProgramStore(store.database, build_id="build") if disk else store
    assert restarted.lookup_invocation(call) == outcome
    with pytest.raises(RecoveryMismatchError):
        restarted.commit_invocation(call, InvocationOutcome(True, value=1))


def test_build_drift_is_scoped_to_the_frozen_root_and_member(tmp_path: Path) -> None:
    path = tmp_path / "member-a.sqlite"
    original = ProgramStore(path, build_id="old")
    original.ensure_root("historical")
    current = ProgramStore(path, build_id="new")
    current.ensure_root("fresh")
    call = invocation(root_id="fresh", build_id="new")
    assert current.begin_invocation(call)
    current.commit_invocation(call, InvocationOutcome(True, value="new result"))
    assert current.lookup_invocation(call).value == "new result"
    with pytest.raises(RecoveryMismatchError):
        current.ensure_root("historical")
    other_member = ProgramStore(tmp_path / "member-b.sqlite", build_id="new")
    other_member.ensure_root("historical")
    assert other_member.begin_invocation(invocation(root_id="historical", build_id="new"))


def test_failed_initial_transaction_leaves_no_schema_or_version(tmp_path: Path) -> None:
    path = tmp_path / "new.sqlite"
    store = ProgramStore(path, build_id="build")
    with pytest.raises(OSError), store._connection() as connection:
        connection.execute("INSERT INTO state VALUES ('x','1')")
        raise OSError("operation failed")
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    assert store.state_get("x") is None


@pytest.mark.parametrize("disk", [False, True])
def test_receipt_nested_key_order_is_stable_through_replay(tmp_path: Path, disk: bool) -> None:
    from tlon.operations.schema import edn_value
    from tlosure.edn import dumps

    store = ProgramStore(tmp_path / "store.sqlite" if disk else None, build_id="build")
    call = invocation()
    value = [
        {
            "source-id": "first",
            "from": "reporter",
            "content": {"z-last": [{"second": 2, "first": 1}], "a-first": "text"},
        },
        {"source-id": "next", "from": "other", "content": {"third": 3, "second": 2}},
    ]
    original_text = dumps(edn_value(value))
    outcome = InvocationOutcome(
        True, value=value, raw_result={"z-receipt": value, "a-status": "committed"}
    )
    store.commit_invocation(call, outcome)
    restarted = ProgramStore(store.database, build_id="build") if disk else store
    replayed = restarted.lookup_invocation(call)
    assert replayed is not None
    assert dumps(edn_value(replayed.value)) == original_text
    assert list(replayed.raw_result) == ["z-receipt", "a-status"]
    assert dumps(edn_value(replayed.raw_result["z-receipt"])) == original_text
    # Re-confirmation must not rewrite a successful receipt or change its key order.
    restarted.commit_invocation(call, outcome)
    assert dumps(edn_value(restarted.lookup_invocation(call).value)) == original_text
