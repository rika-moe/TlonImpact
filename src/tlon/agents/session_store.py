"""Read-only access to archived legacy session events and lifecycle metadata."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote


class SessionStore:
    def __init__(self, database: Path) -> None:
        self.database = database

    def _initialized(self) -> bool:
        if not self.database.exists():
            return False
        with self._connection() as connection:
            return bool(connection.execute("PRAGMA user_version").fetchone()[0])

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            f"{self.database.resolve().as_uri()}?mode=ro", uri=True, timeout=30
        )
        try:
            yield connection
        finally:
            connection.close()

    def events(self, session_id: str) -> list[dict[str, Any]]:
        if not self._initialized():
            return []
        with self._connection() as connection:
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
        with self._connection() as connection:
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
        with self._connection() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM sessions WHERE {where}", arguments
            ).fetchone()
        return int(row[0])

    def latest_completed(self) -> str | None:
        if not self._initialized():
            return None
        with self._connection() as connection:
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
        with self._connection() as connection:
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
        with self._connection() as connection:
            row = connection.execute(
                "SELECT session_id,ended_at FROM sessions WHERE ended_at IS NOT NULL "
                "AND end_reason != 'recovery_blocked' ORDER BY ended_at DESC,rowid DESC LIMIT 1"
            ).fetchone()
        return (row[0], row[1]) if row else None

    def unfinished_ids(self) -> list[str]:
        if not self._initialized():
            return []
        with self._connection() as connection:
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
        with self._connection() as connection:
            row = connection.execute(
                "SELECT end_reason FROM sessions WHERE session_id=? AND ended_at IS NOT NULL",
                (session_id,),
            ).fetchone()
        return str(row[0]) if row is not None else None

    def export_jsonl(self, directory: Path) -> None:
        """Export report artifacts; the database remains the runtime authority."""
        if not self._initialized():
            return
        with self._connection() as connection:
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
                with (directory / f"{quote(session_id, safe='')}.jsonl").open(
                    "w", encoding="utf-8"
                ) as file:
                    file.write(f"{first[0]}\n")
                    for row in rows:
                        file.write(f"{row[0]}\n")
