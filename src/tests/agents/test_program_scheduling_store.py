"""A program's scheduling receipt outlives its current scheduling intent."""

import sqlite3
from pathlib import Path

import pytest

from tlon.agents.scheduling_store import SchedulingStore


def test_receipt_and_intent_commit_together(tmp_path: Path) -> None:
    path = tmp_path / "scheduling.db"
    store = SchedulingStore(path)
    assert store.load() == {}
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TRIGGER stop_intent BEFORE INSERT ON scheduling_intents "
            "BEGIN SELECT RAISE(ABORT, 'crash'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="crash"):
        store.accept_program("member", "run", "sleep", "deadline")
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute("SELECT count(*) FROM program_schedule_receipts").fetchone()[0] == 0
        )
        connection.execute("DROP TRIGGER stop_intent")
    assert store.accept_program("member", "run", "sleep", "deadline") is not None


def test_restart_resumes_only_original_current_intent(tmp_path: Path) -> None:
    path = tmp_path / "scheduling.db"
    store = SchedulingStore(path)
    intent = store.accept_program("member", "one", "sleep", "first")
    restarted = SchedulingStore(path)
    assert restarted.accept_program("member", "one", "sleep", "first") == intent
    restarted.delete("member")
    assert restarted.accept_program("member", "two", "sleep", "second") is not None
    before = restarted.load()
    assert restarted.accept_program("member", "one", "sleep", "first") is None
    assert restarted.load() == before
    with pytest.raises(ValueError, match="changed"):
        restarted.accept_program("member", "one", "exit", None)
