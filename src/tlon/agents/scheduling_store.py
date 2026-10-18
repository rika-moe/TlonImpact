"""Durable sleep/exit intentions; death itself remains the mailbox retired fact."""

import sqlite3
from pathlib import Path


class SchedulingStore:
    """Persist UTC sleep deadlines and committed self-exit intentions."""

    def __init__(self, database: Path) -> None:
        self._database = database

    def _connect(self) -> sqlite3.Connection:
        self._database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database, timeout=30)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS scheduling_intents ("
                "member_id TEXT PRIMARY KEY, action TEXT NOT NULL, wake_at TEXT, session_id TEXT NOT NULL)"
            )
        except Exception:
            connection.close()
            raise
        return connection

    def load(self) -> dict[str, tuple[str, str | None, str]]:
        connection = self._connect()
        try:
            return {
                row[0]: (row[1], row[2], row[3])
                for row in connection.execute(
                    "SELECT member_id, action, wake_at, session_id FROM scheduling_intents"
                )
            }
        finally:
            connection.close()

    def put(self, member_id: str, action: str, wake_at: str | None, session_id: str) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "INSERT INTO scheduling_intents VALUES (?,?,?,?) "
                    "ON CONFLICT(member_id) DO UPDATE SET action=excluded.action, "
                    "wake_at=excluded.wake_at, session_id=excluded.session_id",
                    (member_id, action, wake_at, session_id),
                )
        finally:
            connection.close()

    def delete(self, member_id: str) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute("DELETE FROM scheduling_intents WHERE member_id=?", (member_id,))
        finally:
            connection.close()
