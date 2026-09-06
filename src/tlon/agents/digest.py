"""Digest scheduler — periodic scribe task mails (issue ).

"Tick is a mail": instead of a new timer event type or any change to the
cognitive runtime, this mechanism component periodically sends the scribe
(``scribe_id``, the 书记员 member) one task mail from the ``postmaster``
system address. The mail body carries the full work instructions and the
human recipient list; the scribe's ordinary ``:on :mail-delivered``
cognitive rule fires, its LLM calls the observability tools to gather the
society's activity, and it sends the digest to the humans with
``sendEmail``. A system sender has no mailbox, so no ``sent`` copy
pollutes the channel (see ``organization/mail/delivery.py``).

Mechanism/policy split (same 分工 as PublicHealthMonitor): the scheduler
only delivers the tick; what the scribe does with it is the scribe's
spec, not this component's job.

Owned by ``AgentRegistry`` (like ``IdleReaper`` / ``PublicHealthMonitor``):
constructed with the event bus, the agent info provider (scribe
existence check) and the paths resolver (known-human enumeration), and
its lifecycle runs from the registry's own initialize/shutdown.
"""

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from tlon.base.events.event import Event, EventType
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths, paths
from tlon.organization.mail.mail import POSTMASTER_ADDRESS, Mail, address_for_member

if TYPE_CHECKING:
    from tlon.base.config import AgentInfoProvider
    from tlon.base.events.event_bus import EventBus

logger = get_logger(__name__)

DIGEST_SUBJECT = "社会文摘周期任务"


def _task_content(scribe_id: str, human_addresses: list[str]) -> str:
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


class DigestScheduler:
    """Periodically mails the scribe a digest task ("tick is a mail").

    Every ``interval_seconds`` (default: one day — the daily digest) one
    task mail is published as MAIL_NEW from ``postmaster@tlon.internal``
    to the scribe. The tick is skipped (logged) when the scribe is not
    registered or no human is known — no readers, no digest.

    Dependencies (constructor injected):
        - event_bus: EventBus to publish the task mail
        - agent_info_provider: member enumeration (scribe existence)
        - tlon_paths: known-human resolution (defaults to the module
          singleton)

    ``interval_seconds`` and ``scribe_id`` are constructor knobs (same
    pattern as the health monitor's thresholds): society-wide mechanism
    settings, not per-member config.
    """

    def __init__(
        self,
        event_bus: "EventBus",
        agent_info_provider: "AgentInfoProvider",
        tlon_paths: TlonPaths | None = None,
        *,
        interval_seconds: float = 86400.0,
        scribe_id: str = "ji-shuheng",
    ) -> None:
        self._event_bus = event_bus
        self._agent_info = agent_info_provider
        self._paths = tlon_paths or paths
        self._interval_seconds = interval_seconds
        self._scribe_id = scribe_id

        self._task: asyncio.Task[None] | None = None

    async def initialize(self) -> None:
        """Start the periodic tick task."""
        if self._task is None:
            self._task = asyncio.create_task(self.run())
        logger.info(
            "digest_scheduler_initialized",
            interval_seconds=self._interval_seconds,
            scribe_id=self._scribe_id,
        )

    async def shutdown(self) -> None:
        """Stop the tick task."""
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("digest_scheduler_shutdown")

    async def run(self) -> None:
        """Tick once per interval; a failed tick never kills the loop."""
        while True:
            await asyncio.sleep(self._interval_seconds)
            try:
                await self.tick()
            except Exception as e:
                logger.error("digest_tick_failed", error=str(e))

    async def tick(self) -> Mail | None:
        """Send one digest task mail to the scribe.

        Returns the published Mail, or None when the tick was skipped
        (scribe not registered / no known humans).
        """
        if self._scribe_id not in self._agent_info.get_all_ids():
            logger.info("digest_tick_skipped", reason="scribe_not_registered")
            return None
        human_ids = self._paths.known_human_ids()
        if not human_ids:
            logger.info("digest_tick_skipped", reason="no_known_humans")
            return None

        human_addresses = [address_for_member(human_id) for human_id in human_ids]
        mail = Mail.create(
            from_address=POSTMASTER_ADDRESS,
            to=[address_for_member(self._scribe_id)],
            subject=DIGEST_SUBJECT,
            content=_task_content(self._scribe_id, human_addresses),
        )
        await self._event_bus.publish(
            Event(type=EventType.MAIL_NEW, payload=mail, source="digest_scheduler")
        )
        logger.info(
            "digest_task_sent",
            mail_id=mail.id,
            scribe_id=self._scribe_id,
            humans=human_addresses,
        )
        return mail
