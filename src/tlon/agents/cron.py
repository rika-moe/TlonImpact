"""Declared periodic mail delivery, configured exclusively by cron.yaml.

Each static entry sends a postmaster mail on an interval or cron schedule.
The recipient's ordinary Lisp program decides what the task means. Missing
configuration creates no tasks; invalid entries and failed ticks are isolated.
AgentRegistry owns the scheduler's initialize/shutdown lifecycle.
"""

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import yaml
from croniter import croniter

from tlon.base.execution import bind_execution
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import POSTMASTER_ADDRESS, Mail, address_for_member
from tlon.organization.mail.post_office import PostOffice
from tlon.organization.mail.store import MailStore

if TYPE_CHECKING:
    from tlon.base.config import AgentInfoProvider
    from tlon.base.events.event_bus import EventBus

logger = get_logger(__name__)


@dataclass(frozen=True)
class CronEntry:
    """One periodic task: at each scheduled tick, mail ``to`` from postmaster.

    Exactly one of ``interval_seconds`` / ``cron`` is set (validated by
    the cron.yaml loader).
    """

    name: str
    to: str
    subject: str
    content: str
    interval_seconds: float | None = None
    cron: str | None = None


class CronScheduler:
    """Deliver declared periodic mails, with one isolated loop per entry."""

    def __init__(
        self,
        event_bus: "EventBus",
        agent_info_provider: "AgentInfoProvider",
        tlon_paths: TlonPaths,
        *,
        post_office: PostOffice | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._agent_info = agent_info_provider
        self._paths = tlon_paths
        self._post_office = post_office
        self._entries: list[CronEntry] = []
        self._tasks: list[asyncio.Task[None]] = []

    async def initialize(self) -> None:
        """Load declared entries once and start their loops."""
        if self._tasks:
            return
        self._entries = self._load_declared()
        with bind_execution(None):
            self._tasks = [asyncio.create_task(self._run_entry(entry)) for entry in self._entries]
        logger.info(
            "cron_scheduler_initialized",
            entries=[entry.name for entry in self._entries],
        )

    async def shutdown(self) -> None:
        """Stop all entry loops."""
        for task in self._tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        logger.info("cron_scheduler_shutdown")

    # ==================== Declared entries (cron.yaml) ====================

    def _load_declared(self) -> list[CronEntry]:
        """Parse ``~/.tlon/cron.yaml`` into entries; invalid ones are dropped.

        An absent file is the normal state (no declared entries). A file
        that cannot be parsed as a list is rejected wholesale with a
        warning; within a valid list each entry is validated on its own,
        so one bad entry never affects the others.
        """
        cron_file = self._paths.cron_file()
        if not cron_file.exists():
            return []
        try:
            data = yaml.safe_load(cron_file.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            logger.warning("cron_file_unreadable", error=str(e))
            return []
        if data is None:
            return []
        if not isinstance(data, list):
            logger.warning("cron_file_invalid", reason="top_level_not_list")
            return []
        entries: list[CronEntry] = []
        for raw in data:
            entry = self._parse_declared(raw)
            if entry is not None:
                entries.append(entry)
        return entries

    def _parse_declared(self, raw: object) -> CronEntry | None:
        """Validate one declared entry; returns None (with a warning) if bad."""
        if not isinstance(raw, dict):
            logger.warning("cron_entry_invalid", reason="not_a_mapping", entry=repr(raw))
            return None
        name = raw.get("name")
        to = raw.get("to")
        subject = raw.get("subject")
        content = raw.get("content")
        if not (
            isinstance(name, str)
            and name
            and isinstance(to, str)
            and to
            and isinstance(subject, str)
            and subject
            and isinstance(content, str)
            and content
        ):
            logger.warning(
                "cron_entry_invalid",
                reason="missing_or_empty_fields",
                entry=name if isinstance(name, str) else repr(raw),
            )
            return None
        interval = raw.get("interval_seconds")
        cron = raw.get("cron")
        if (interval is None) == (cron is None):
            logger.warning("cron_entry_invalid", reason="exactly_one_schedule_required", entry=name)
            return None
        interval_seconds: float | None = None
        cron_expression: str | None = None
        if interval is not None:
            if (
                not isinstance(interval, (int, float))
                or isinstance(interval, bool)
                or interval <= 0
            ):
                logger.warning("cron_entry_invalid", reason="bad_interval_seconds", entry=name)
                return None
            interval_seconds = float(interval)
        else:
            if not isinstance(cron, str):
                logger.warning("cron_entry_invalid", reason="bad_cron", entry=name)
                return None
            try:
                croniter(cron)
            except (ValueError, KeyError) as e:
                logger.warning("cron_entry_invalid", reason="bad_cron", entry=name, error=str(e))
                return None
            cron_expression = cron
        return CronEntry(
            name=name,
            to=to,
            subject=subject,
            content=content,
            interval_seconds=interval_seconds,
            cron=cron_expression,
        )

    # ==================== Scheduling ====================

    async def _run_entry(self, entry: CronEntry) -> None:
        """Tick one entry per its schedule; a failed tick never kills the loop."""
        while True:
            await asyncio.sleep(self._seconds_until_next(entry))
            try:
                await self.tick(entry)
            except Exception as e:
                logger.error("cron_tick_failed", entry=entry.name, error=str(e))

    @staticmethod
    def _seconds_until_next(entry: CronEntry) -> float:
        """Delay until the entry's next tick (interval or cron, local time)."""
        if entry.interval_seconds is not None:
            return entry.interval_seconds
        now = datetime.now().astimezone()
        assert entry.cron is not None  # guaranteed by entry validation
        nxt = croniter(entry.cron, now).get_next(datetime)
        return max(0.0, (nxt - now).total_seconds())

    async def tick(self, entry: CronEntry) -> Mail | None:
        """Send one task mail for an entry.

        Returns the published Mail, or None when the tick was skipped
        (target member unknown).
        """
        if not await asyncio.to_thread(self._member_exists, entry.to):
            logger.info(
                "cron_tick_skipped", entry=entry.name, reason="member_not_found", to=entry.to
            )
            return None
        mail = Mail.create(
            from_address=POSTMASTER_ADDRESS,
            to=[address_for_member(entry.to)],
            subject=entry.subject,
            content=entry.content,
        )
        if self._post_office is None:
            raise RuntimeError("Cron scheduler has no post office")
        await self._post_office.post(mail)
        logger.info("cron_task_sent", entry=entry.name, mail_id=mail.id, to=entry.to)
        return mail

    def _member_exists(self, member_id: str) -> bool:
        """Target existence: agent ids from the registry, humans from disk."""
        if member_id.startswith("human:"):
            return member_id in MailStore(self._paths.mail_database_file()).known_human_ids()
        return member_id in self._agent_info.get_all_ids()
