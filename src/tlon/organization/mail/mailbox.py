"""Per-member mailbox storage for Tlon.

A mailbox is a directory holding three files:

- ``inbox.jsonl``   — received mail, append-only
- ``sent.jsonl``    — mail sent by this member, append-only
- ``inbox.cursor``  — one read mail id per line (the read model)

Read state is a set of ids rather than a per-mail flag: jsonl files are
append-only, so flipping a flag would mean rewriting the file, while a
cursor of read ids stays correct even when mail is processed out of
order. A missing cursor file means everything is unread.

The mailbox directory is resolved by the caller (via
``tlon.base.paths``); Mailbox itself knows nothing about layout policy.

Multiple Mailbox instances over the same directory (the delivery
component's cache, the REST ``/inbox`` handler, an agent's runtime)
share one asyncio lock per directory, so concurrent writers are
serialized process-wide even though each caller constructs its own
instance.

``unread()`` scans incrementally: a per-directory cache (module-level,
bounded LRU, same discipline as the lock table) remembers how far
``inbox.jsonl`` was parsed and the mails produced, so a rescan only
parses the appended tail instead of the whole file. The cache is
validated against the file's identity (device + inode) and size on
every call — a replaced or truncated inbox falls back to a full scan.
Only complete lines are consumed: a trailing partial line (a crash-torn
append) is left for the next scan, preserving the skip-and-warn
semantics of malformed lines.
"""

import asyncio
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import aiofiles

from tlon.base.logger import get_logger
from tlon.organization.mail.mail import Mail

logger = get_logger(__name__)

#: Bound on the shared lock table (oldest entries evicted, same
#: magnitude as ``delivery._SEEN_CAP``).
_LOCKS_CAP = 4096

#: Directory-keyed locks shared by every Mailbox instance over the same
#: directory. Bounded LRU (``_LOCKS_CAP``): the least recently used
#: unlocked entry is evicted on overflow; a lock that is currently
#: locked (held or awaited) is never evicted, since evicting it would
#: break mutual exclusion for its waiters. An evicted directory simply
#: gets a fresh lock on its next Mailbox — safe because nothing was
#: waiting on the old one.
_LOCKS: OrderedDict[Path, asyncio.Lock] = OrderedDict()


def _lock_for(mail_dir: Path) -> asyncio.Lock:
    """Return the shared lock for a directory (bounded LRU).

    Hits are refreshed to most-recently-used. On overflow the oldest
    unlocked entry is evicted; locked entries are skipped (if every
    entry is locked the table temporarily grows past ``_LOCKS_CAP``).
    """
    lock = _LOCKS.get(mail_dir)
    if lock is not None:
        _LOCKS.move_to_end(mail_dir)
        return lock
    lock = asyncio.Lock()
    _LOCKS[mail_dir] = lock
    if len(_LOCKS) > _LOCKS_CAP:
        for path, candidate in _LOCKS.items():
            if path == mail_dir:
                continue  # never evict the lock we are about to return
            if not candidate.locked():
                del _LOCKS[path]
                break
    return lock


#: Bound on the inbox scan cache (oldest entries evicted, same
#: magnitude as ``_LOCKS_CAP``).
_SCAN_CAP = 4096


@dataclass
class _InboxScan:
    """Incremental parse state for one ``inbox.jsonl``.

    ``offset`` is the byte position just past the last complete line
    consumed — a trailing partial line (a write torn by a crash) is not
    consumed and is retried on the next scan. ``lines`` counts every
    physical line consumed so far, so malformed-line warnings keep
    file-wide line numbers. ``mails`` holds every mail parsed up to
    ``offset``, in file order (a scan holds an inbox's parsed content
    in memory; the directory-level ``_SCAN_CAP`` bounds how many
    inboxes are held).
    """

    dev: int
    ino: int
    offset: int
    lines: int
    mails: list[Mail] = field(default_factory=list)


#: Directory-keyed scan cache shared by every Mailbox instance over the
#: same directory. Bounded LRU (``_SCAN_CAP``): an evicted directory
#: simply falls back to a full scan on its next ``unread()``.
_SCANS: OrderedDict[Path, _InboxScan] = OrderedDict()


class Mailbox:
    """File-based mailbox for a single member.

    Thread-safe (asyncio): file mutations hold a lock shared by all
    instances rooted at the same directory.
    """

    def __init__(self, mail_dir: Path):
        """Initialize the mailbox rooted at the given directory.

        Pure construction: the directory is created lazily at the first
        write (append / mark_read), so read-only use (inbox scans,
        stats, reputation) never materializes anything.

        Args:
            mail_dir: Directory holding inbox.jsonl / sent.jsonl /
                inbox.cursor
        """
        self._dir = mail_dir
        self._inbox_path = mail_dir / "inbox.jsonl"
        self._sent_path = mail_dir / "sent.jsonl"
        self._cursor_path = mail_dir / "inbox.cursor"
        self._lock = _lock_for(mail_dir)

    async def append_inbox(self, mail: Mail) -> None:
        """Append a received mail to the inbox.

        Args:
            mail: The mail to store
        """
        await self._append(self._inbox_path, mail)

    async def append_sent(self, mail: Mail) -> None:
        """Append a sent mail to the sent file.

        Args:
            mail: The mail to store
        """
        await self._append(self._sent_path, mail)

    async def _append(self, path: Path, mail: Mail) -> None:
        """Persist a single mail to a jsonl file."""
        async with self._lock:
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
                async with aiofiles.open(path, "a", encoding="utf-8") as f:
                    await f.write(json.dumps(mail.to_dict(), ensure_ascii=False) + "\n")
            except Exception as e:
                logger.error(
                    "mail_persist_failed",
                    mail_id=mail.id,
                    file_path=str(path),
                    error=str(e),
                    exc_info=True,
                )
                raise

    async def find(self, mail_id: str) -> Mail | None:
        """Look up a mail by id across inbox and sent.

        Args:
            mail_id: The mail id to find

        Returns:
            The mail, or None when neither file holds it
        """
        inbox = await self._read_inbox()
        sent = await self._read_mails(self._sent_path)
        for mail in [*inbox, *sent]:
            if mail.id == mail_id:
                return mail
        return None

    async def inbox(self) -> list[Mail]:
        """List all mails in the inbox, in file order.

        Backed by the incremental scan cache (see :meth:`_read_inbox`),
        so repeat scans only parse the appended tail. Used by the
        mail-stats provider.

        Returns:
            Every parseable mail in inbox.jsonl, oldest first
        """
        return await self._read_inbox()

    async def sent(self) -> list[Mail]:
        """List all mails in the sent file, in file order.

        Full scan (no incremental cache — sent volumes are small). Used by
        the public-health monitor's orphan-mail scan.

        Returns:
            Every parseable mail in sent.jsonl, oldest first
        """
        return await self._read_mails(self._sent_path)

    async def thread_incoming(self, thread_id: str) -> list[Mail]:
        """List inbox mails belonging to one thread, in chronological order.

        Used by the context builder's ``thread-recap`` section to locate
        the thread's earlier session triggers (a session is only ever
        opened for an incoming mail). Backed by the incremental inbox
        scan cache, so repeat scans only parse the appended tail.

        Args:
            thread_id: The thread root mail id

        Returns:
            Inbox mails with this thread id, oldest first
        """
        inbox = await self._read_inbox()
        return [mail for mail in inbox if mail.thread_id == thread_id]

    async def unread(self) -> list[Mail]:
        """List inbox mail whose id is not yet in the read cursor.

        The inbox is scanned incrementally (see :meth:`_read_inbox`);
        the cursor is read in full on every call.

        Returns:
            Unread mail in chronological order
        """
        read_ids = await self._read_cursor()
        inbox = await self._read_inbox()
        unread = [mail for mail in inbox if mail.id not in read_ids]
        return sorted(unread, key=lambda mail: mail.timestamp)

    async def mark_read(self, mail_id: str) -> None:
        """Mark a mail as read by appending its id to the cursor.

        Idempotent: marking an already-read mail is a no-op.

        Args:
            mail_id: The id of the mail that has been processed
        """
        async with self._lock:
            if mail_id in await self._read_cursor():
                return
            self._dir.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self._cursor_path, "a", encoding="utf-8") as f:
                await f.write(mail_id + "\n")

    async def recent(self, limit: int = 50) -> list[Mail]:
        """List the most recent mail across inbox and sent.

        Used by the context builder to show the member's own
        correspondence.

        Args:
            limit: Maximum number of mails to return

        Returns:
            Merged inbox + sent mail in chronological order, capped at
            the most recent ``limit`` entries
        """
        inbox = await self._read_mails(self._inbox_path)
        sent = await self._read_mails(self._sent_path)
        merged = sorted(inbox + sent, key=lambda mail: mail.timestamp)
        return merged[-limit:]

    async def _read_inbox(self) -> list[Mail]:
        """Read inbox mails, parsing only the appended tail when possible.

        The per-directory scan cache (``_SCANS``) is validated against
        the file's identity (device + inode) and size on every call: a
        replaced or truncated inbox falls back to a full scan, a missing
        or unreadable inbox reads as empty (same contract as
        :meth:`_read_mails`). The directory-shared lock serializes cache
        updates across Mailbox instances.
        """
        async with self._lock:
            try:
                stat = self._inbox_path.stat()
            except FileNotFoundError:
                _SCANS.pop(self._dir, None)
                return []
            except OSError as e:
                logger.error(
                    "mail_file_read_failed", file_path=str(self._inbox_path), error=str(e)
                )
                return []

            scan = _SCANS.get(self._dir)
            if scan is not None and (scan.dev, scan.ino) != (stat.st_dev, stat.st_ino):
                logger.info("mail_inbox_rescan", file_path=str(self._inbox_path), reason="replaced")
                scan = None
            elif scan is not None and scan.offset > stat.st_size:
                logger.info("mail_inbox_rescan", file_path=str(self._inbox_path), reason="truncated")
                scan = None

            start = scan.offset if scan is not None else 0
            try:
                async with aiofiles.open(self._inbox_path, "rb") as f:
                    await f.seek(start)
                    chunk = await f.read()
            except Exception as e:
                logger.error(
                    "mail_file_read_failed", file_path=str(self._inbox_path), error=str(e)
                )
                return []

            # Only complete lines are consumed: a trailing partial line
            # (a crash-torn append) stays unconsumed and is retried on
            # the next scan.
            complete = chunk[: chunk.rfind(b"\n") + 1]
            try:
                text = complete.decode("utf-8")
            except UnicodeDecodeError as e:
                logger.error(
                    "mail_file_read_failed", file_path=str(self._inbox_path), error=str(e)
                )
                return []

            scan = scan if scan is not None else _InboxScan(stat.st_dev, stat.st_ino, 0, 0)
            for line_number, line in enumerate(text.splitlines(), start=scan.lines):
                if not line.strip():
                    continue
                try:
                    scan.mails.append(Mail.from_dict(json.loads(line)))
                except Exception as e:
                    logger.warning(
                        "mail_parse_failed",
                        file_path=str(self._inbox_path),
                        line_number=line_number,
                        error=str(e),
                    )
            scan.lines += len(text.splitlines())
            scan.offset = start + len(complete)

            _SCANS[self._dir] = scan
            _SCANS.move_to_end(self._dir)
            if len(_SCANS) > _SCAN_CAP:
                _SCANS.popitem(last=False)
            return list(scan.mails)

    async def _read_mails(self, path: Path) -> list[Mail]:
        """Read all mails from a jsonl file, skipping malformed lines."""
        if not path.exists():
            return []

        try:
            async with aiofiles.open(path, encoding="utf-8") as f:
                content = await f.read()
        except Exception as e:
            logger.error("mail_file_read_failed", file_path=str(path), error=str(e))
            return []

        mails = []
        for line_num, line in enumerate(content.splitlines()):
            if not line.strip():
                continue
            try:
                mails.append(Mail.from_dict(json.loads(line)))
            except Exception as e:
                logger.warning(
                    "mail_parse_failed",
                    file_path=str(path),
                    line_number=line_num,
                    error=str(e),
                )
        return mails

    async def _read_cursor(self) -> set[str]:
        """Read the set of read mail ids from the cursor file."""
        if not self._cursor_path.exists():
            return set()

        try:
            async with aiofiles.open(self._cursor_path, encoding="utf-8") as f:
                content = await f.read()
        except Exception as e:
            logger.error(
                "mail_cursor_read_failed", file_path=str(self._cursor_path), error=str(e)
            )
            return set()

        return {line.strip() for line in content.splitlines() if line.strip()}
