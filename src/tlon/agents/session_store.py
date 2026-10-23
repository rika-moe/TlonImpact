"""SQLite authority for session events and lifecycle metadata (ADR 0032)."""

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    started_at TEXT,
    ended_at TEXT,
    end_reason TEXT,
    last_seq INTEGER NOT NULL DEFAULT -1
);
CREATE TABLE IF NOT EXISTS events (
    session_id TEXT NOT NULL REFERENCES sessions(session_id),
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    event_json TEXT NOT NULL,
    PRIMARY KEY(session_id, seq)
);
CREATE INDEX IF NOT EXISTS sessions_completed ON sessions(ended_at, session_id);
CREATE INDEX IF NOT EXISTS sessions_started ON sessions(started_at, session_id);
"""

_INITIALIZE_LOCK = threading.Lock()


class SessionStore:
    def __init__(self, database: Path) -> None:
        self.database = database

    def initialize(self) -> None:
        with _INITIALIZE_LOCK:
            self.database.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.database, timeout=30)
            try:
                if connection.execute("PRAGMA user_version").fetchone()[0] == 0:
                    connection.execute("PRAGMA journal_mode=WAL")
                    connection.executescript(
                        "BEGIN IMMEDIATE;" + _SCHEMA + "PRAGMA user_version=1; COMMIT;"
                    )
            finally:
                connection.close()

    def _initialized(self) -> bool:
        if not self.database.exists():
            return False
        with self._connection(write=False) as connection:
            return bool(connection.execute("PRAGMA user_version").fetchone()[0])

    @contextmanager
    def _connection(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        if write:
            self.initialize()
            connection = sqlite3.connect(self.database, timeout=30)
        else:
            connection = sqlite3.connect(
                f"{self.database.resolve().as_uri()}?mode=ro", uri=True, timeout=30
            )
        try:
            if write:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def append(self, session_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Commit one event and its lifecycle metadata atomically."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO sessions(session_id) VALUES (?)", (session_id,)
            )
            row = connection.execute(
                "SELECT last_seq FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            if row is None:
                raise RuntimeError("session row missing after insert")
            seq = int(row[0]) + 1
            ts = datetime.now(UTC).isoformat()
            event = {
                "seq": seq,
                "ts": ts,
                "type": event_type,
                "session_id": session_id,
                **payload,
            }
            connection.execute(
                "INSERT INTO events(session_id,seq,ts,type,event_json) VALUES (?,?,?,?,?)",
                (
                    session_id,
                    seq,
                    ts,
                    event_type,
                    json.dumps(event, ensure_ascii=False),
                ),
            )
            if event_type == "session/start":
                connection.execute(
                    "UPDATE sessions SET last_seq=?,started_at=COALESCE(started_at,?) "
                    "WHERE session_id=?",
                    (seq, ts, session_id),
                )
            elif event_type in ("session/end", "session/recovery-blocked"):
                connection.execute(
                    "UPDATE sessions SET last_seq=?,ended_at=?,end_reason=? WHERE session_id=?",
                    (
                        seq,
                        ts,
                        "recovery_blocked"
                        if event_type == "session/recovery-blocked"
                        else str(payload.get("reason", "unknown")),
                        session_id,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE sessions SET last_seq=? WHERE session_id=?", (seq, session_id)
                )
        return event

    def events(self, session_id: str) -> list[dict[str, Any]]:
        if not self._initialized():
            return []
        with self._connection(write=False) as connection:
            rows = connection.execute(
                "SELECT event_json FROM events WHERE session_id=? ORDER BY seq", (session_id,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    @staticmethod
    def _completed_filter(started_after: datetime | None) -> tuple[str, tuple[str, ...]]:
        where = "ended_at IS NOT NULL AND end_reason != 'recovery_blocked'"
        arguments: tuple[str, ...] = ()
        if started_after is not None:
            where += " AND (started_at IS NULL OR started_at > ?)"
            arguments = (started_after.isoformat(),)
        return where, arguments

    def completed_ids(self, started_after: datetime | None = None) -> list[str]:
        if not self._initialized():
            return []
        where, arguments = self._completed_filter(started_after)
        with self._connection(write=False) as connection:
            return [
                row[0]
                for row in connection.execute(
                    f"SELECT session_id FROM sessions WHERE {where} ORDER BY session_id",
                    arguments,
                )
            ]

    def count_completed(self, started_after: datetime | None = None) -> int:
        if not self._initialized():
            return 0
        where, arguments = self._completed_filter(started_after)
        with self._connection(write=False) as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM sessions WHERE {where}", arguments
            ).fetchone()
        return int(row[0])

    def latest_completed(self) -> str | None:
        if not self._initialized():
            return None
        with self._connection(write=False) as connection:
            row = connection.execute(
                "SELECT session_id FROM sessions WHERE ended_at IS NOT NULL AND end_reason != 'recovery_blocked' "
                "ORDER BY ended_at DESC,rowid DESC LIMIT 1"
            ).fetchone()
        return row[0] if row is not None else None

    def completed_metadata(self, started_after: datetime | None = None) -> list[tuple[str, str]]:
        """Completed source IDs and terminal clocks without reading event bodies."""
        if not self._initialized():
            return []
        where, arguments = self._completed_filter(started_after)
        with self._connection(write=False) as connection:
            return [
                (row[0], row[1])
                for row in connection.execute(
                    f"SELECT session_id,ended_at FROM sessions WHERE {where}", arguments
                )
            ]

    def latest_completed_metadata(self) -> tuple[str, str] | None:
        """Latest legacy source and completion clock without reading event bodies."""
        if not self._initialized():
            return None
        with self._connection(write=False) as connection:
            row = connection.execute(
                "SELECT session_id,ended_at FROM sessions WHERE ended_at IS NOT NULL "
                "AND end_reason != 'recovery_blocked' ORDER BY ended_at DESC,rowid DESC LIMIT 1"
            ).fetchone()
        return (row[0], row[1]) if row else None

    def unfinished_ids(self) -> list[str]:
        if not self._initialized():
            return []
        with self._connection(write=False) as connection:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT session_id FROM sessions "
                    "WHERE started_at IS NOT NULL AND ended_at IS NULL ORDER BY session_id"
                )
            ]

    def end_reason(self, session_id: str) -> str | None:
        if not self._initialized():
            return None
        with self._connection(write=False) as connection:
            row = connection.execute(
                "SELECT end_reason FROM sessions WHERE session_id=? AND ended_at IS NOT NULL",
                (session_id,),
            ).fetchone()
        return str(row[0]) if row is not None else None

    def export_jsonl(self, directory: Path) -> None:
        """Export report artifacts; the database remains the runtime authority."""
        if not self._initialized():
            return
        with self._connection(write=False) as connection:
            session_ids = [
                row[0]
                for row in connection.execute("SELECT session_id FROM sessions ORDER BY session_id")
            ]
            for session_id in session_ids:
                rows = connection.execute(
                    "SELECT event_json FROM events WHERE session_id=? ORDER BY seq",
                    (session_id,),
                )
                first = rows.fetchone()
                if first is None:
                    continue
                directory.mkdir(parents=True, exist_ok=True)
                with (directory / f"{session_id}.jsonl").open("w", encoding="utf-8") as file:
                    file.write(f"{first[0]}\n")
                    for row in rows:
                        file.write(f"{row[0]}\n")
