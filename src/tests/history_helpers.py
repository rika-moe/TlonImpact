"""Construct archived legacy evidence in tests; production journals are read-only."""

import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tlon.agents.session_journal import SessionJournal

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


class LegacyHistoryFixture:
    def __init__(self, database: Path) -> None:
        self.database = database

    def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("PRAGMA user_version=1")
            connection.executescript(_SCHEMA)
            connection.commit()

    def append(self, session_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Commit one event and its lifecycle metadata atomically."""
        self.initialize()
        with closing(sqlite3.connect(self.database)) as connection:
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
            connection.commit()
        return event


async def append_history(
    journal: SessionJournal, session_id: str, event_type: str, **payload: Any
) -> None:
    await asyncio.to_thread(
        LegacyHistoryFixture(journal._store.database).append, session_id, event_type, payload
    )


def insert_legacy_run(
    database: Path, run_id: str, source: str = "{}", input_edn: str = "{}"
) -> None:
    """Insert retired evidence into an already initialized program database."""
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO runs(run_id,rule_edn,input_edn) VALUES (?,?,?)",
            (run_id, source, input_edn),
        )
        connection.commit()
