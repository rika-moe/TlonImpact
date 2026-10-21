"""Entry snapshots and completion are the single authority across restarts."""

import sqlite3
from pathlib import Path

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.operations.schema import InputError, RecoveryMismatchError
from tlosure.edn import Keyword, loads


@pytest.fixture(params=[False, True], ids=["memory", "sqlite"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> ProgramStore:
    return ProgramStore(tmp_path / "program.db" if request.param else None, build_id="build")


def package() -> dict[str, object]:
    return {"sources": {"work.reply": "(ns work.reply)"}, "fuel": 100, "budget": 12}


def reopen(store: ProgramStore) -> ProgramStore:
    return ProgramStore(store.database, build_id="build") if store.database else store


def test_source_input_and_constraints_are_frozen(store: ProgramStore) -> None:
    frozen = store.ensure_entry("run", "handler", "{:input 1}", package())
    changed = package()
    changed["fuel"] = 999
    assert reopen(store).ensure_entry("run", "changed", "nil", changed) == frozen
    assert reopen(store).pending() == [frozen]
    assert loads(frozen.package_edn)[Keyword("fuel")] == 100


def test_completion_result_and_intent_survive_restart(store: ProgramStore) -> None:
    store.ensure_entry("run", "handler", "nil", package())
    result = {Keyword("result"): [1, None]}
    completed = store.complete_entry("run", result, {"kind": "sleep-until", "at": "soon"})
    restored = reopen(store)
    assert restored.pending() == []
    assert restored.get_run("run") == completed
    assert restored.pending_scheduling() == [completed]
    assert loads(completed.result_edn) == result
    assert restored.complete_entry("run", "changed", None) == completed
    restored.mark_scheduling_applied("run")
    restored.mark_scheduling_applied("run")
    assert reopen(restored).pending_scheduling() == []
    assert reopen(restored).get_run("run").scheduling_applied


def test_nil_result_is_a_completed_result_without_intent(store: ProgramStore) -> None:
    store.ensure_entry("run", "handler", "nil", package())
    run = store.complete_entry("run", None)
    assert run.completed and run.result_edn == "nil"
    assert store.pending_scheduling() == []
    with pytest.raises(RecoveryMismatchError, match="not committed"):
        store.mark_scheduling_applied("run")


def test_invalid_return_does_not_partially_complete(store: ProgramStore) -> None:
    before = store.ensure_entry("run", "handler", "nil", package())
    with pytest.raises(InputError):
        store.complete_entry("run", {"bad": object()}, {"kind": "exit"})
    assert store.get_run("run") == before
    with pytest.raises(InputError):
        store.complete_entry("run", "ok", {"bad": object()})
    assert store.get_run("run") == before


def test_failed_entries_preserve_package_and_cannot_complete(store: ProgramStore) -> None:
    before = store.ensure_entry("run", "handler", "nil", package())
    store.fail("run", "failure")
    assert store.get_run("run").package_edn == before.package_edn
    assert store.pending() == []
    with pytest.raises(RecoveryMismatchError):
        store.complete_entry("run", "ok")


def test_entry_cannot_reinterpret_rule_run(store: ProgramStore) -> None:
    store.ensure("run", "rule", "nil")
    with pytest.raises(RecoveryMismatchError, match="not a module entry"):
        store.ensure_entry("run", "handler", "nil", package())


def test_entry_recovery_rejects_changed_host(tmp_path: Path) -> None:
    path = tmp_path / "program.db"
    ProgramStore(path, build_id="old").ensure_entry("run", "handler", "nil", package())
    with pytest.raises(RecoveryMismatchError, match="build changed"):
        ProgramStore(path, build_id="new").ensure_entry("run", "handler", "nil", package())


def test_sql_failure_rolls_back_completion_and_intent(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "program.db", build_id="build")
    before = store.ensure_entry("run", "handler", "nil", package())
    with sqlite3.connect(store.database) as connection:
        connection.execute(
            "CREATE TRIGGER stop_completion BEFORE UPDATE ON entry_programs "
            "BEGIN SELECT RAISE(ABORT, 'crash'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="crash"):
        store.complete_entry("run", "ok", {"kind": "exit"})
    assert reopen(store).get_run("run") == before
    assert reopen(store).pending_scheduling() == []


def test_sql_failure_rolls_back_input_source_and_root(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "program.db", build_id="build")
    assert store.pending() == []  # Initialize the database before injecting failure.
    with sqlite3.connect(store.database) as connection:
        connection.execute(
            "CREATE TRIGGER stop_entry BEFORE INSERT ON entry_programs "
            "BEGIN SELECT RAISE(ABORT, 'crash'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="crash"):
        store.ensure_entry("run", "handler", "nil", package())
    assert reopen(store).get_run("run") is None
    with sqlite3.connect(store.database) as connection:
        assert connection.execute("SELECT count(*) FROM roots").fetchone()[0] == 0
