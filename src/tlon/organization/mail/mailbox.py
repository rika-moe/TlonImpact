"""Async member-scoped mailbox backed by the shared SQLite post office."""

import asyncio
from pathlib import Path

from tlon.organization.mail.mail import Mail
from tlon.organization.mail.store import MailStore


class Mailbox:
    def __init__(self, database: Path, member_id: str) -> None:
        self._store = MailStore(database)
        self.member_id = member_id

    async def append_inbox(self, mail: Mail, *, gateway: bool = False) -> None:
        await asyncio.to_thread(self._store.add_to_inbox, mail, self.member_id, gateway)

    async def append_sent(self, mail: Mail) -> None:
        await asyncio.to_thread(self._store.append_sent, mail, self.member_id)

    async def inbox(self) -> list[Mail]:
        return await asyncio.to_thread(self._store.mails, self.member_id, "inbox")

    async def sent(self) -> list[Mail]:
        return await asyncio.to_thread(self._store.mails, self.member_id, "sent")

    async def unread(self) -> list[Mail]:
        return await asyncio.to_thread(self._store.mails, self.member_id, "unread")

    async def mark_read(self, mail_id: str) -> None:
        await asyncio.to_thread(self._store.mark_read, self.member_id, mail_id)

    async def find(self, mail_id: str) -> Mail | None:
        return await asyncio.to_thread(self._store.find, self.member_id, mail_id)

    async def thread_incoming(self, thread_id: str) -> list[Mail]:
        return await asyncio.to_thread(self._store.thread_incoming, self.member_id, thread_id)

    async def recent(self, limit: int = 50) -> list[Mail]:
        return await asyncio.to_thread(self._store.recent, self.member_id, limit)

    async def earliest(self, limit: int = 50) -> list[Mail]:
        return await asyncio.to_thread(self._store.earliest, self.member_id, limit)
