"""Transactional webhook inbox and immutable outgoing mail plans."""

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class MailPlan:
    member_id: str
    mail_id: str
    address: str
    created_at: str
    subject: str
    body: str


@dataclass(frozen=True)
class PendingMail:
    event_key: str
    plan: MailPlan


def stable_mail_id(event_key: str, member_id: str) -> str:
    return "gitea-" + hashlib.sha256(json.dumps([event_key, member_id]).encode()).hexdigest()


class WebhookStore:
    """Each operation owns a connection; acceptance is one SQLite transaction."""

    def __init__(self, database: Path) -> None:
        self.database = database

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=10)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    event_key TEXT PRIMARY KEY, payload BLOB NOT NULL, unrouted TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                    delivery_id TEXT PRIMARY KEY, event_key TEXT NOT NULL REFERENCES events,
                    body_digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outgoing (
                    event_key TEXT NOT NULL REFERENCES events, member_id TEXT NOT NULL,
                    plan TEXT NOT NULL, complete INTEGER NOT NULL DEFAULT 0, outcome TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                    PRIMARY KEY (event_key, member_id)
                );
            """)
        self.database.chmod(0o600)

    def accept(
        self,
        event_key: str,
        delivery_id: str,
        raw_body: bytes,
        plans: Sequence[MailPlan],
        *,
        unrouted: Sequence[str] = (),
    ) -> bool:
        digest = hashlib.sha256(raw_body).hexdigest()
        if not event_key or not delivery_id:
            raise ValueError("Webhook event and delivery IDs are required")
        if len({plan.member_id for plan in plans}) != len(plans):
            raise ValueError("Duplicate webhook recipients")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT event_key, body_digest FROM deliveries WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            if previous:
                if previous != (event_key, digest):
                    raise ValueError("Webhook delivery ID was reused with different content")
                return False
            inserted = (
                connection.execute(
                    "INSERT OR IGNORE INTO events VALUES (?, ?, ?)",
                    (event_key, raw_body, json.dumps(sorted(set(unrouted)))),
                ).rowcount
                == 1
            )
            connection.execute(
                "INSERT INTO deliveries VALUES (?, ?, ?)", (delivery_id, event_key, digest)
            )
            if inserted:
                connection.executemany(
                    "INSERT INTO outgoing (event_key, member_id, plan) VALUES (?, ?, ?)",
                    [(event_key, plan.member_id, json.dumps(asdict(plan))) for plan in plans],
                )
            return inserted

    def pending(self, limit: int = 100) -> list[PendingMail]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_key, plan FROM outgoing WHERE complete = 0 ORDER BY attempts, rowid LIMIT ?",
                (limit,),
            ).fetchall()
        return [PendingMail(key, MailPlan(**json.loads(plan))) for key, plan in rows]

    def complete(self, event_key: str, member_id: str, *, outcome: str = "delivered") -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE outgoing SET complete = 1, outcome = ?, last_error = NULL WHERE event_key = ? AND member_id = ?",
                (outcome, event_key, member_id),
            )

    def fail(self, event_key: str, member_id: str, error: str) -> None:
        """Caller supplies a safe diagnostic, never raw exceptions or credentials."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE outgoing SET attempts = attempts + 1, last_error = ? "
                "WHERE event_key = ? AND member_id = ? AND complete = 0",
                (error[:300], event_key, member_id),
            )

    def status(self) -> dict[str, object]:
        with self._connect() as connection:
            unrouted_rows = [
                (row[0], json.loads(row[1]))
                for row in connection.execute(
                    "SELECT event_key, unrouted FROM events WHERE unrouted != '[]' ORDER BY rowid DESC"
                ).fetchall()
            ]
            return {
                "unrouted_users": sum(len(names) for _, names in unrouted_rows),
                "recent_unrouted": [
                    {"event_key": key, "usernames": names} for key, names in unrouted_rows[:20]
                ],
                "accepted": connection.execute("SELECT count(*) FROM events").fetchone()[0],
                "deliveries": connection.execute("SELECT count(*) FROM deliveries").fetchone()[0],
                "failed": connection.execute(
                    "SELECT count(*) FROM outgoing WHERE complete = 0 AND last_error IS NOT NULL"
                ).fetchone()[0],
                "unrouted": connection.execute(
                    "SELECT count(*) FROM events e WHERE NOT EXISTS "
                    "(SELECT 1 FROM outgoing o WHERE o.event_key = e.event_key)"
                ).fetchone()[0],
                "recent_failures": [
                    {"event_key": row[0], "member_id": row[1], "attempts": row[2], "error": row[3]}
                    for row in connection.execute(
                        "SELECT event_key, member_id, attempts, last_error FROM outgoing "
                        "WHERE complete = 0 AND last_error IS NOT NULL ORDER BY rowid DESC LIMIT 20"
                    ).fetchall()
                ],
                "recent_deliveries": [
                    {"delivery_id": row[0], "event_key": row[1]}
                    for row in connection.execute(
                        "SELECT delivery_id, event_key FROM deliveries ORDER BY rowid DESC LIMIT 20"
                    ).fetchall()
                ],
                "pending": connection.execute(
                    "SELECT count(*) FROM outgoing WHERE complete = 0"
                ).fetchone()[0],
            }
