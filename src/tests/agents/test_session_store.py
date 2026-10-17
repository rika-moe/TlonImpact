"""SQLite session event and lifecycle storage (ADR 0032)."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tlon.agents.session_store import SessionStore


def test_failed_append_rolls_back_event_sequence_and_end_metadata(tmp_path: Path) -> None:
    database = tmp_path / "sessions.sqlite3"
    store = SessionStore(database)
    session_id = "agent1-s"
    store.append(session_id, "session/start", {"context": []})
    store.append(session_id, "iteration", {"value": 1})
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_end BEFORE UPDATE OF end_reason ON sessions
            WHEN NEW.session_id = 'agent1-s' AND NEW.end_reason = 'boom'
            BEGIN
                SELECT RAISE(ABORT, 'forced end failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced end failure"):
        store.append(session_id, "session/end", {"reason": "boom"})

    assert [event["seq"] for event in store.events(session_id)] == [0, 1]
    assert store.end_reason(session_id) is None
    assert store.latest_completed() is None
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT last_seq,ended_at,end_reason FROM sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
    assert row == (1, None, None)

    completed = store.append(session_id, "session/end", {"reason": "complete"})
    assert completed["seq"] == 2
    assert store.end_reason(session_id) == "complete"


def test_concurrent_connections_allocate_conflict_free_sequences(tmp_path: Path) -> None:
    database = tmp_path / "sessions.sqlite3"
    store = SessionStore(database)
    session_id = "agent1-concurrent"
    store.append(session_id, "session/start", {"context": []})

    def append_batch(first_value: int) -> None:
        connection_store = SessionStore(database)
        for value in range(first_value, first_value + 8):
            connection_store.append(session_id, "iteration", {"value": value})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(append_batch, range(0, 32, 8)))

    events = store.events(session_id)
    assert [event["seq"] for event in events] == list(range(33))
    assert sorted(event["value"] for event in events[1:]) == list(range(32))


def test_completed_filter_keeps_sessions_without_start_pending(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    store.append("agent1-dated", "session/start", {"context": []})
    store.append("agent1-dated", "session/end", {"reason": "complete"})
    store.append("agent1-timeless", "session/end", {"reason": "complete"})
    future = datetime(2999, 1, 1, tzinfo=UTC)

    assert store.count_completed() == len(store.completed_ids()) == 2
    pending = store.completed_ids(started_after=future)
    assert pending == ["agent1-timeless"]
    assert store.count_completed(started_after=future) == len(pending) == 1


def test_export_jsonl_writes_report_events_without_empty_directories(tmp_path: Path) -> None:
    missing = SessionStore(tmp_path / "missing.sqlite3")
    missing.export_jsonl(tmp_path / "missing-archive")
    assert not (tmp_path / "missing-archive").exists()

    store = SessionStore(tmp_path / "sessions.sqlite3")
    archive = tmp_path / "archive" / "sessions"
    store.initialize()
    store.export_jsonl(archive)
    assert not archive.exists()

    store.append("agent1-a", "session/start", {"context": []})
    store.append("agent1-a", "session/end", {"reason": "complete"})
    store.append("agent1-b", "session/start", {"context": []})
    store.append("agent1-b", "iteration", {"value": 2})

    store.export_jsonl(archive)

    assert sorted(path.name for path in archive.glob("*.jsonl")) == [
        "agent1-a.jsonl",
        "agent1-b.jsonl",
    ]
    for session_id in ("agent1-a", "agent1-b"):
        lines = (archive / f"{session_id}.jsonl").read_text(encoding="utf-8").splitlines()
        assert [json.loads(line) for line in lines] == store.events(session_id)
