"""SQLite session event and lifecycle storage (ADR 0032)."""

import json
from datetime import UTC, datetime
from pathlib import Path

from tests.history_helpers import LegacyHistoryFixture
from tlon.agents.session_store import SessionStore


def test_completed_filter_keeps_sessions_without_start_pending(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.sqlite3")
    LegacyHistoryFixture(store.database).append("agent1-dated", "session/start", {"context": []})
    LegacyHistoryFixture(store.database).append(
        "agent1-dated", "session/end", {"reason": "complete"}
    )
    LegacyHistoryFixture(store.database).append(
        "agent1-timeless", "session/end", {"reason": "complete"}
    )
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
    LegacyHistoryFixture(store.database).initialize()
    store.export_jsonl(archive)
    assert not archive.exists()

    LegacyHistoryFixture(store.database).append("agent1-a", "session/start", {"context": []})
    LegacyHistoryFixture(store.database).append("agent1-a", "session/end", {"reason": "complete"})
    LegacyHistoryFixture(store.database).append("agent1-b", "session/start", {"context": []})
    LegacyHistoryFixture(store.database).append("agent1-b", "iteration", {"value": 2})

    store.export_jsonl(archive)

    assert sorted(path.name for path in archive.glob("*.jsonl")) == [
        "agent1-a.jsonl",
        "agent1-b.jsonl",
    ]
    for session_id in ("agent1-a", "agent1-b"):
        lines = (archive / f"{session_id}.jsonl").read_text(encoding="utf-8").splitlines()
        assert [json.loads(line) for line in lines] == store.events(session_id)


def test_history_queries_leave_database_unchanged(tmp_path: Path) -> None:
    database = tmp_path / "sessions.sqlite3"
    fixture = LegacyHistoryFixture(database)
    fixture.append("done", "session/start", {})
    fixture.append("done", "session/end", {"reason": "complete"})
    original = database.read_bytes()
    store = SessionStore(database)
    assert store.events("done")
    assert store.completed_ids() == ["done"]
    assert store.count_completed() == 1
    assert store.latest_completed() == "done"
    assert store.completed_metadata()
    assert store.latest_completed_metadata()
    assert store.unfinished_ids() == []
    assert store.end_reason("done") == "complete"
    store.export_jsonl(tmp_path / "archive")
    assert database.read_bytes() == original
    assert not hasattr(store, "append")
    assert not hasattr(store, "initialize")
