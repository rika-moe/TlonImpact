"""Cron scheduler — declarative periodic task mails (docs/design/mail.md
「周期任务：tick 即邮件」).

"Tick is a mail", generalized: instead of a new timer event type or any
change to the cognitive runtime, each periodic task is one mail from the
``postmaster`` system address to a target member, fired on a schedule.
The mail body carries the full work instructions; the recipient's
ordinary ``:on :mail-delivered`` cognitive rule fires. A system sender
has no mailbox, so no ``sent`` copy pollutes the channel (see
``organization/mail/delivery.py``).

Entries come from two sources, merged and managed uniformly:

1. **Built-in entries** (registered in code, content may be generated
   dynamically at tick time). The daily digest is the instance: its
   content factory computes the human recipient list from
   ``known_human_ids()`` at each tick — behavior unchanged from the
   ``DigestScheduler``, which this module replaces.
2. **Declared entries** (``~/.tlon/cron.yaml``, read once at initialize —
   edits take effect on restart). Each entry is
   ``{name, schedule, to, subject, content}`` with a static body and a
   schedule of either ``interval_seconds: N`` (every N seconds, first
   tick after N seconds too) or ``cron: "分 时 日 月 周"`` (standard
   5-field cron, local timezone, resolved via ``croniter``). An absent
   file means no declared entries — the normal state.

Skip semantics (logged, never raised): the target member does not exist
(agent via ``get_all_ids()``, human via ``known_human_ids()``), or the
content factory returns None (e.g. the digest has no known humans — no
readers, no digest). An invalid declared entry (missing fields, both or
neither schedule, unparseable cron) is rejected at initialize with a
warning and does not affect other entries.

Mechanism/policy split (same 分工 as PublicHealthMonitor): the scheduler
only delivers the tick; what the recipient does with it is the member's
spec, not this component's job.

Owned by ``AgentRegistry`` (like ``IdleReaper`` / ``PublicHealthMonitor``):
constructed with the event bus, the agent info provider and the paths
resolver, and its lifecycle runs from the registry's own
initialize/shutdown.
"""

import asyncio
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import yaml
from croniter import croniter

from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import POSTMASTER_ADDRESS, Mail, address_for_member
from tlon.organization.mail.post_office import PostOffice

if TYPE_CHECKING:
    from tlon.base.config import AgentInfoProvider
    from tlon.base.events.event_bus import EventBus

logger = get_logger(__name__)

DIGEST_SUBJECT = "社会文摘周期任务"

#: Tick-time content factory. Returns the mail body, or None to skip this
#: tick (e.g. the digest has no known humans to send to). Only built-in
#: entries generate content dynamically; declared entries wrap a static str.
ContentFactory = Callable[[], "str | None"]


@dataclass(frozen=True)
class CronEntry:
    """One periodic task: at each scheduled tick, mail ``to`` from postmaster.

    Exactly one of ``interval_seconds`` / ``cron`` is set (validated by
    the cron.yaml loader; built-in entries are constructed valid).
    """

    name: str
    to: str
    subject: str
    content: ContentFactory
    interval_seconds: float | None = None
    cron: str | None = None


def _digest_task_content(scribe_id: str, human_addresses: list[str]) -> str:
    """The scribe's self-contained work instructions for one digest tick."""
    recipients = "\n".join(f"- {address}" for address in human_addresses)
    return f"""这是系统发出的周期性任务邮件（digest scheduler）。你（{scribe_id}，书记员）本期任务：把全社会的近期活动蒸馏成一份文摘，发给全体人类成员——人类从「读全部邮件」退为「读摘要 + 处置例外」，你的文摘就是他们的主要入口。

可用工具（全部只读，按需要调用，不必全用）：
- getMetrics：社会指标快照（LLM 调用量、邮件量等计数）。
- listTraces / getTrace：近期各成员的会话与工具调用轨迹、token 消耗——看谁做了什么、代价多大。
- getReputation：各成员的声誉与邮件行为（收发量、回复率、致谢）。
- getMailStats：你自己的通信统计。
- getSocialHealth：孤儿邮件清单——发出多天无回复、可能需要人类介入推动的邮件。
- listStagingPrototypes：待人类批准的原型提案（agent 写好、:trusted false，等人类决策）。

文效应覆盖三块内容：
1. 谁做了什么：本期各成员的主要活动与值得一提的进展（简洁，一事一句）。
2. 待人类决策事项：待批准的原型提案、等人类拍板的问题——逐条列出，附必要的上下文。
3. 孤儿邮件与异常：无回复的旧邮件、噪声或其他需要人类留意的异常。

本期人类收件人地址（全发）：
{recipients}

发信方式：一封 sendEmail，to 填以上全部人类地址，is_complete=true。不要回复本邮件（postmaster 是系统地址，没有邮箱）。"""


class CronScheduler:
    """Fires periodic task mails ("tick is a mail") for built-in + declared entries.

    Each entry gets its own loop task: per-entry isolation means one
    broken entry never starves the others, and a failed tick never kills
    its own loop (logged and the schedule continues).

    Dependencies (constructor injected):
        - event_bus: EventBus to publish the task mail
        - agent_info_provider: member enumeration (target existence)
        - tlon_paths: cron.yaml resolution + known-human enumeration

    ``interval_seconds`` / ``scribe_id`` configure the built-in digest
    entry (same knobs as 's DigestScheduler): society-wide mechanism
    settings, not per-member config. ``builtin_entries`` replaces the
    digest entirely when given (tests and future built-ins).
    """

    def __init__(
        self,
        event_bus: "EventBus",
        agent_info_provider: "AgentInfoProvider",
        tlon_paths: TlonPaths,
        *,
        post_office: PostOffice | None = None,
        interval_seconds: float = 86400.0,
        scribe_id: str = "ji-shuheng",
        builtin_entries: list[CronEntry] | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._agent_info = agent_info_provider
        self._paths = tlon_paths
        self._post_office = post_office
        if builtin_entries is not None:
            self._builtin_entries = builtin_entries
        else:
            self._builtin_entries = [
                CronEntry(
                    name="digest",
                    to=scribe_id,
                    subject=DIGEST_SUBJECT,
                    content=self._digest_content(scribe_id),
                    interval_seconds=interval_seconds,
                )
            ]

        self._entries: list[CronEntry] = []
        self._tasks: list[asyncio.Task[None]] = []

    def _digest_content(self, scribe_id: str) -> ContentFactory:
        """Digest factory: recompute the human recipient list at each tick.

        Returns None (skip) when no human is known — no readers, no digest.
        """

        def factory() -> str | None:
            human_ids = self._paths.known_human_ids()
            if not human_ids:
                return None
            addresses = [address_for_member(human_id) for human_id in human_ids]
            return _digest_task_content(scribe_id, addresses)

        return factory

    async def initialize(self) -> None:
        """Load entries (built-in + declared, read once) and start the loops."""
        if self._tasks:
            return
        self._entries = [*self._builtin_entries, *self._load_declared()]
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
            content=lambda: content,
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
        (target member unknown / content factory declined).
        """
        if not self._member_exists(entry.to):
            logger.info(
                "cron_tick_skipped", entry=entry.name, reason="member_not_found", to=entry.to
            )
            return None
        content = entry.content()
        if content is None:
            logger.info("cron_tick_skipped", entry=entry.name, reason="content_unavailable")
            return None

        mail = Mail.create(
            from_address=POSTMASTER_ADDRESS,
            to=[address_for_member(entry.to)],
            subject=entry.subject,
            content=content,
        )
        if self._post_office is None:
            raise RuntimeError("Cron scheduler has no post office")
        await self._post_office.post(mail)
        logger.info("cron_task_sent", entry=entry.name, mail_id=mail.id, to=entry.to)
        return mail

    def _member_exists(self, member_id: str) -> bool:
        """Target existence: agent ids from the registry, humans from disk."""
        if member_id.startswith("human:"):
            return member_id in self._paths.known_human_ids()
        return member_id in self._agent_info.get_all_ids()
