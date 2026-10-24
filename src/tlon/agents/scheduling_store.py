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
                "member_id TEXT PRIMARY KEY, action TEXT NOT NULL, wake_at TEXT, run_id TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS program_schedule_receipts ("
                "member_id TEXT NOT NULL, run_id TEXT NOT NULL, action TEXT NOT NULL, wake_at TEXT, "
                "PRIMARY KEY(member_id,run_id))"
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
                    "SELECT member_id, action, wake_at, run_id FROM scheduling_intents"
                )
            }
        finally:
            connection.close()

    def put(self, member_id: str, action: str, wake_at: str | None, run_id: str) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    "INSERT INTO scheduling_intents VALUES (?,?,?,?) "
                    "ON CONFLICT(member_id) DO UPDATE SET action=excluded.action, "
                    "wake_at=excluded.wake_at, run_id=excluded.run_id",
                    (member_id, action, wake_at, run_id),
                )
        finally:
            connection.close()

    def accept_program(
        self,
        member_id: str,
        run_id: str,
        action: str,
        wake_at: str | None,
        *,
        blocked: bool = False,
    ) -> tuple[str, str | None, str] | None:
        """Accept a program intent once, atomically with its durable receipt.

        A receipt survives wake, replacement and retirement. Replays may only
        resume the original still-current intent; they never recreate it.
        Rejected requests are consumed too, so later replay cannot supersede
        an administrator's decision after that decision has been applied.
        """
        connection = self._connect()
        origin = f"program:{run_id}"
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                receipt = connection.execute(
                    "SELECT action,wake_at FROM program_schedule_receipts "
                    "WHERE member_id=? AND run_id=?",
                    (member_id, run_id),
                ).fetchone()
                current = connection.execute(
                    "SELECT action,wake_at,run_id FROM scheduling_intents WHERE member_id=?",
                    (member_id,),
                ).fetchone()
                if receipt is not None:
                    if receipt != (action, wake_at):
                        raise ValueError("Program scheduling request changed")
                    return current if not blocked and current == (action, wake_at, origin) else None
                connection.execute(
                    "INSERT INTO program_schedule_receipts VALUES (?,?,?,?)",
                    (member_id, run_id, action, wake_at),
                )
                if blocked or current is not None or action == "finish":
                    return None
                intent = (action, wake_at, origin)
                connection.execute(
                    "INSERT INTO scheduling_intents VALUES (?,?,?,?)", (member_id, *intent)
                )
                return intent
        finally:
            connection.close()

    def delete(self, member_id: str) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute("DELETE FROM scheduling_intents WHERE member_id=?", (member_id,))
        finally:
            connection.close()
