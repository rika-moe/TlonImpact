"""Transactional authority for mail; notifications are deliberately separate."""

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tlon.organization.mail.mail import Mail


class IdempotencyConflict(ValueError):  # noqa: N818
    """A request key was reused for different content."""


class RetiredRecipients(ValueError):  # noqa: N818
    """One or more recipients retired before the transaction committed."""

    def __init__(self, member_ids: list[str]) -> None:
        self.member_ids = member_ids
        super().__init__(f"Retired recipients: {', '.join(member_ids)}")


@dataclass
class StoredPost:
    mail: Mail
    request_id: str
    local_recipients: list[str]
    gateways: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        parts: list[str] = []
        if self.local_recipients:
            parts.append(
                f"邮件已存入 {', '.join(self.local_recipients)} 的信箱，不代表已经阅读或处理。"
            )
        for gateway in self.gateways:
            address, status = gateway["address"], gateway["status"]
            if status == "delivered":
                parts.append(f"网关 {address} 信箱投递成功，不代表外部平台已收到。")
            elif status == "failed":
                parts.append(
                    f"网关 {address} 信箱投递失败：{gateway.get('error') or '未提供原因'}。"
                )
            else:
                parts.append(
                    f"网关 {address} 投递结果未知，不能确认是否已尝试或成功；不会自动重试。"
                )
        return {
            "mail_id": self.mail.id,
            "request_id": self.request_id,
            "local_recipients": self.local_recipients,
            "gateways": self.gateways,
            "message": "".join(parts),
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    member_id TEXT PRIMARY KEY, retired INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
    mail_id TEXT PRIMARY KEY, body TEXT NOT NULL, sender_member TEXT,
    timestamp TEXT NOT NULL, thread_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_timeline ON messages(timestamp);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_id, timestamp);
CREATE INDEX IF NOT EXISTS messages_sender ON messages(sender_member, timestamp);
CREATE TABLE IF NOT EXISTS recipients (
    member_id TEXT NOT NULL REFERENCES members(member_id),
    mail_id TEXT NOT NULL REFERENCES messages(mail_id),
    read INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'local' CHECK(kind IN ('local', 'gateway')),
    PRIMARY KEY(member_id, mail_id)
);
CREATE INDEX IF NOT EXISTS recipients_mail ON recipients(mail_id, member_id);
CREATE INDEX IF NOT EXISTS recipients_unread ON recipients(member_id, read, mail_id);
CREATE TABLE IF NOT EXISTS requests (
    sender_address TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
    mail_id TEXT NOT NULL UNIQUE REFERENCES messages(mail_id),
    PRIMARY KEY(sender_address, request_id)
);
CREATE TABLE IF NOT EXISTS gateways (
    mail_id TEXT NOT NULL REFERENCES messages(mail_id), address TEXT NOT NULL,
    status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', PRIMARY KEY(mail_id, address)
);
"""


# journal_mode changes do not consistently honor SQLite busy_timeout. Serialize
# first-time initialization across store instances before write transactions.
_INITIALIZE_LOCK = threading.Lock()


class MailStore:
    def __init__(self, database: Path) -> None:
        self.database = database

    def initialize(self) -> None:
        """Create the complete schema atomically before accepting service traffic."""
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
        """An existing file is not visible as a mailbox until schema commit."""
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

    @staticmethod
    def _post(connection: sqlite3.Connection, mail_id: str) -> StoredPost:
        body = connection.execute(
            "SELECT body FROM messages WHERE mail_id=?", (mail_id,)
        ).fetchone()
        request = connection.execute(
            "SELECT request_id FROM requests WHERE mail_id=?", (mail_id,)
        ).fetchone()
        recipients = connection.execute(
            "SELECT member_id FROM recipients WHERE mail_id=? AND kind='local' ORDER BY member_id",
            (mail_id,),
        ).fetchall()
        gateways = connection.execute(
            "SELECT address,status,error FROM gateways WHERE mail_id=? ORDER BY address", (mail_id,)
        ).fetchall()
        return StoredPost(
            Mail.from_dict(json.loads(body[0])),
            request[0],
            [row[0] for row in recipients],
            [dict(zip(("address", "status", "error"), row, strict=True)) for row in gateways],
        )

    def get_request(
        self, sender_address: str, request_id: str, fingerprint: str
    ) -> StoredPost | None:
        if not self._initialized():
            return None
        with self._connection(write=False) as connection:
            return self._request(connection, sender_address, request_id, fingerprint)

    def _request(
        self, connection: sqlite3.Connection, sender: str, request_id: str, fingerprint: str
    ) -> StoredPost | None:
        row = connection.execute(
            "SELECT fingerprint,mail_id FROM requests WHERE sender_address=? AND request_id=?",
            (sender, request_id),
        ).fetchone()
        if row is None:
            return None
        if row[0] != fingerprint:
            raise IdempotencyConflict("request_id already used for different content")
        return self._post(connection, row[1])

    @staticmethod
    def _message(connection: sqlite3.Connection, mail: Mail, sender_id: str | None) -> None:
        body = json.dumps(mail.to_dict(), ensure_ascii=False, sort_keys=True)
        existing = connection.execute(
            "SELECT body FROM messages WHERE mail_id=?", (mail.id,)
        ).fetchone()
        if existing is not None and existing[0] != body:
            raise ValueError("mail_id already used for different content")
        connection.execute(
            "INSERT OR IGNORE INTO messages VALUES (?,?,?,?,?)",
            (mail.id, body, sender_id, mail.timestamp, mail.thread_id),
        )
        if sender_id is not None:
            connection.execute(
                "UPDATE messages SET sender_member=? WHERE mail_id=?", (sender_id, mail.id)
            )

    def accept(
        self,
        mail: Mail,
        sender_id: str | None,
        recipient_ids: list[str],
        request_id: str,
        fingerprint: str,
        gateways: list[str],
    ) -> tuple[StoredPost, bool]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            replay = self._request(connection, mail.from_address, request_id, fingerprint)
            if replay is not None:
                return replay, False
            retired = [
                member
                for member in dict.fromkeys(recipient_ids)
                if connection.execute(
                    "SELECT retired FROM members WHERE member_id=?", (member,)
                ).fetchone()
                == (1,)
            ]
            if retired:
                raise RetiredRecipients(retired)
            self._message(connection, mail, sender_id)
            for member in dict.fromkeys(recipient_ids):
                connection.execute("INSERT OR IGNORE INTO members(member_id) VALUES (?)", (member,))
                connection.execute(
                    "INSERT INTO recipients(member_id,mail_id) VALUES (?,?)", (member, mail.id)
                )
            connection.execute(
                "INSERT INTO requests VALUES (?,?,?,?)",
                (mail.from_address, request_id, fingerprint, mail.id),
            )
            for address in dict.fromkeys(gateways):
                connection.execute(
                    "INSERT INTO gateways(mail_id,address,status) VALUES (?,?,'unknown')",
                    (mail.id, address),
                )
            return self._post(connection, mail.id), True

    def update_gateway(
        self, mail_id: str, address: str, status: str, error: str = ""
    ) -> StoredPost:
        with self._connection() as connection:
            connection.execute(
                "UPDATE gateways SET status=?,error=? WHERE mail_id=? AND address=?",
                (status, error, mail_id, address),
            )
            return self._post(connection, mail_id)

    def register_member(self, member_id: str) -> None:
        with self._connection() as connection:
            connection.execute("INSERT OR IGNORE INTO members(member_id) VALUES (?)", (member_id,))

    def retire(self, member_id: str) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO members VALUES (?,1) ON CONFLICT(member_id) DO UPDATE SET retired=1",
                (member_id,),
            )

    def member_exists(self, member_id: str) -> bool:
        if not self._initialized():
            return False
        with self._connection(write=False) as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM members WHERE member_id=?", (member_id,)
                ).fetchone()
                is not None
            )

    def is_retired(self, member_id: str) -> bool:
        if not self._initialized():
            return False
        with self._connection(write=False) as connection:
            row = connection.execute(
                "SELECT retired FROM members WHERE member_id=?", (member_id,)
            ).fetchone()
            return row is not None and bool(row[0])

    def known_human_ids(self) -> list[str]:
        if not self._initialized():
            return []
        with self._connection(write=False) as connection:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT member_id FROM members WHERE member_id LIKE 'human:%' ORDER BY member_id"
                )
            ]

    def add_to_inbox(self, mail: Mail, member_id: str, gateway: bool = False) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._message(connection, mail, None)
            connection.execute("INSERT OR IGNORE INTO members(member_id) VALUES (?)", (member_id,))
            connection.execute(
                "INSERT OR IGNORE INTO recipients(member_id,mail_id,kind) VALUES (?,?,?)",
                (member_id, mail.id, "gateway" if gateway else "local"),
            )

    def append_sent(self, mail: Mail, member_id: str) -> None:
        with self._connection() as connection:
            self._message(connection, mail, member_id)

    def mails(self, member_id: str, view: str = "inbox") -> list[Mail]:
        if view == "sent":
            return self._query("sender_member=?", (member_id,))
        if view == "all":
            return self._query(self._visible(), (member_id, member_id))
        unread = " AND r.read=0" if view == "unread" else ""
        if view not in ("inbox", "unread"):
            raise ValueError(f"Unknown mailbox view: {view}")
        return self._query(
            f"m.mail_id IN (SELECT r.mail_id FROM recipients r WHERE r.member_id=?{unread})",
            (member_id,),
        )

    @staticmethod
    def _visible() -> str:
        return "(m.sender_member=? OR EXISTS (SELECT 1 FROM recipients r WHERE r.mail_id=m.mail_id AND r.member_id=?))"

    def _query(
        self,
        condition: str,
        parameters: tuple[str, ...],
        *,
        limit: int | None = None,
        newest: bool = False,
    ) -> list[Mail]:
        if not self._initialized() or (limit is not None and limit <= 0):
            return []
        direction = "DESC" if newest else "ASC"
        sql = f"SELECT body FROM messages m WHERE {condition} ORDER BY timestamp {direction}, m.rowid {direction}"
        arguments: tuple[str | int, ...] = parameters
        if limit is not None:
            sql += " LIMIT ?"
            arguments = (*parameters, limit)
        with self._connection(write=False) as connection:
            mails = [
                Mail.from_dict(json.loads(row[0])) for row in connection.execute(sql, arguments)
            ]
        return list(reversed(mails)) if newest else mails

    def find(self, member_id: str, mail_id: str) -> Mail | None:
        matches = self._query(
            f"m.mail_id=? AND {self._visible()}", (mail_id, member_id, member_id), limit=1
        )
        return matches[0] if matches else None

    def thread_incoming(self, member_id: str, thread_id: str) -> list[Mail]:
        return self._query(
            "m.thread_id=? AND EXISTS (SELECT 1 FROM recipients r WHERE r.mail_id=m.mail_id AND r.member_id=?)",
            (thread_id, member_id),
        )

    def recent(self, member_id: str, limit: int = 50) -> list[Mail]:
        return self._query(self._visible(), (member_id, member_id), limit=limit, newest=True)

    def earliest(self, member_id: str, limit: int = 50) -> list[Mail]:
        return self._query(self._visible(), (member_id, member_id), limit=limit)

    def unread_member_ids(self) -> list[str]:
        """Recovery scan of active mailboxes, without loading message bodies."""
        if not self._initialized():
            return []
        with self._connection(write=False) as connection:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT r.member_id FROM recipients r JOIN members m USING(member_id) "
                    "WHERE r.read=0 AND r.kind='local' AND m.retired=0 ORDER BY r.member_id"
                )
            ]

    def mark_read(self, member_id: str, mail_id: str) -> None:
        if not self.database.exists():
            return
        with self._connection() as connection:
            connection.execute(
                "UPDATE recipients SET read=1 WHERE member_id=? AND mail_id=?", (member_id, mail_id)
            )
