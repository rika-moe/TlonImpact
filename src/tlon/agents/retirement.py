"""Retirement ceremony: farewell letter and task handover
(docs/design/mail.md 「退休交接信」).

When a member is deliberately stopped (``AGENT_STOPPED``, same trigger as
the LessonsDistiller — never the scale-to-zero ``AGENT_SLEEPING`` drain),
its unfinished business should not vanish with it: this component takes an
**offline** inventory of the retired member's archive — unfinished tasks
(todo/doing/paused), unreplied mail threads, and the memory digest — and,
when there is anything to hand over or to say, runs one LLM pass writing a
farewell letter (tenure summary + unfinished items + handover list +
advice for the successor). The letter is mailed **from the retired
member's own address** (retirement is sleep: the member stays in the
registry, its address still resolves, and the sent copy lands in its
archive) to its spawner, or to the guest human when it was not spawned by
another member.

Whether or not a letter goes out, an ``AGENT_RETIRED`` fact is published
carrying the handover inventory — visibility never depends on the LLM.
Task *claiming* is deliberately not automated: the letter delivers the
list (nothing is lost at death); picking items up is the recipient's
(member's or human's) decision.

Skip conditions (same discipline as LessonsDistiller — this is post-hoc
bookkeeping, the stop is already done):
- administrative removal (``removed=True`` in the payload, set by
  ``AgentRegistry.remove_member``) — removal is not retirement: the
  member's home is already deleted, there is no archive to inventory,
  so the whole ceremony is skipped and no AGENT_RETIRED goes out
- nothing to hand over (non-cognitive member with no unfinished
  tasks/threads, or an empty archive) — the event still goes out
- LLM failure — logged, never raised; the event goes out with
  ``farewell_sent=false``
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from tlon.agents.lessons import build_retiree_llm_client
from tlon.agents.member_spec import MemberSpec
from tlon.agents.session_journal import SessionJournal
from tlon.base.component import Component
from tlon.base.config import yaml_load_file
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.organization.mail.mail import Mail, address_for_member
from tlon.organization.mail.mailbox import Mailbox
from tlon.tools.tasks.manager import TaskManager
from tlon.tools.tasks.models import TaskStatus
from tlosure.edn import loads

logger = get_logger(__name__)

#: Fallback recipient when the retired member was not spawned by another
#: member (standard team members): the guest human.
GUEST_HUMAN_ID = "human:guest"

FAREWELL_SYSTEM_PROMPT = (
    "你是一位即将退休的 Agent 成员，正在写告别信。你已被永久停止运行，"
    "这封信是你留给社会的最后一份记录。\n\n"
    "信的内容必须覆盖四个部分：\n"
    "1. 任期总结——你做过什么、记住了什么（基于给出的记忆）\n"
    "2. 未竟事项——没做完的任务与没回复的邮件线程，逐条列出\n"
    "3. 移交清单——每件事项建议由谁、如何接手\n"
    "4. 给继任者的建议——从经验中提炼的简短忠告\n\n"
    "用中文、Markdown 写作，语气克制诚恳。不要虚构清单之外的事项。"
)


def _parse_timestamp(value: str) -> datetime | None:
    """Parse a Mail timestamp; naive values read as UTC, junk as None."""
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


class RetirementCeremony(Component, Device):
    """Hands over a retired member's unfinished business
    (docs/design/mail.md 「退休交接信」).

    A pure event consumer in the LessonsDistiller mold: subscribes to
    ``AGENT_STOPPED`` in ``initialize()``, unsubscribes in ``shutdown()``.
    The bus awaits async handlers in the subscription's own consumer
    task, so a slow ceremony never blocks the stop flow.

    Dependencies (constructor injected):
        - event_bus: EventBus to subscribe to
        - tlon_paths: path resolver (tests inject an isolated home)
        - llm_client_factory: ``agent_id -> LLMClient``; the default
          rebuilds the retired member's config from its archived
          ``agent.yaml`` (tests inject a canned-response fake)
    """

    def __init__(
        self,
        event_bus: EventBus,
        tlon_paths: TlonPaths,
        llm_client_factory: Callable[[str], LLMClient] | None = None,
    ):
        Component.__init__(self, name="RetirementCeremony")
        Device.__init__(self, event_bus=event_bus, name="RetirementCeremony")
        self._paths = tlon_paths
        self._client_factory = llm_client_factory or (
            lambda agent_id: build_retiree_llm_client(agent_id, tlon_paths)
        )

    async def initialize(self) -> None:
        """Start the ceremony by subscribing to AGENT_STOPPED."""
        self.subscribe(EventType.AGENT_STOPPED, self._on_agent_stopped)
        self._initialized = True
        logger.info("retirement_ceremony_initialized")

    async def shutdown(self) -> None:
        """Stop the ceremony by unsubscribing from the bus."""
        self.unsubscribe_all()
        self._initialized = False
        logger.info("retirement_ceremony_shutdown")

    async def _on_agent_stopped(self, event: Event) -> None:
        """Hold one stopped member's ceremony; never raised failures.

        Administrative removal is not retirement: ``removed=True`` in the
        payload (AgentRegistry.remove_member) means the member's home was
        already deleted, so there is no archive to inventory — the whole
        ceremony is skipped (no inventory, no farewell, no AGENT_RETIRED).
        """
        agent_id = ""
        stopped_by = ""
        removed = False
        if isinstance(event.payload, dict):
            agent_id = str(event.payload.get("agent_id") or "")
            stopped_by = str(event.payload.get("stopped_by") or "")
            removed = bool(event.payload.get("removed"))
        if not agent_id:
            logger.warning("retirement_no_agent_id")
            return
        if removed:
            logger.info("retirement_skipped_removed", agent_id=agent_id)
            return
        try:
            await self._hold_ceremony(agent_id, stopped_by)
        except Exception as e:
            logger.warning("retirement_ceremony_failed", agent_id=agent_id, error=str(e))

    async def _hold_ceremony(self, agent_id: str, stopped_by: str) -> None:
        """Inventory the archive, maybe mail a farewell, always publish
        the AGENT_RETIRED fact."""
        config = yaml_load_file(self._paths.agent_config_file(agent_id))
        name = config.name if config is not None else agent_id

        open_tasks = self._open_tasks(agent_id)
        open_threads = await self._open_threads(agent_id)
        digest = await self._memory_digest(agent_id)
        has_digest = digest is not None and digest["claim_count"] > 0

        farewell_sent = False
        farewell_to: str | None = None
        if has_digest or open_tasks or open_threads:
            farewell_to = self._recipient_address(config.spawned_by if config else None)
            farewell_sent = await self._send_farewell(
                agent_id, name, farewell_to, open_tasks, open_threads, digest
            )
            if not farewell_sent:
                farewell_to = None
        else:
            logger.info("retirement_farewell_skipped", agent_id=agent_id, reason="empty_handover")

        await self.publish(
            Event(
                type=EventType.AGENT_RETIRED,
                payload={
                    "agent_id": agent_id,
                    "name": name,
                    "stopped_by": stopped_by,
                    "open_tasks": open_tasks,
                    "open_threads": [t["subject"] for t in open_threads],
                    "farewell_sent": farewell_sent,
                    "farewell_to": farewell_to,
                },
                source="retirement_ceremony",
            )
        )
        logger.info(
            "agent_retired",
            agent_id=agent_id,
            open_tasks=len(open_tasks),
            open_threads=len(open_threads),
            farewell_sent=farewell_sent,
        )

    # ── Offline inventory (the member runtime is already shut down) ──

    def _is_cognitive(self, agent_id: str) -> bool:
        """Whether the archived spec makes this a cognitive member (any
        rule carries a ``:loop``). Same skip discipline as the
        LessonsDistiller: a missing/unparseable behavior file means there
        is no memory worth reading."""
        behavior_file = self._paths.agent_behavior_file(agent_id)
        if not behavior_file.exists():
            return False
        try:
            spec = MemberSpec.from_edn(loads(behavior_file.read_text(encoding="utf-8")))
        except Exception as e:
            logger.warning("retirement_spec_unreadable", agent_id=agent_id, error=str(e))
            return False
        return any(rule.loop is not None for rule in spec.rules)

    def _open_tasks(self, agent_id: str) -> list[str]:
        """Unfinished task titles (todo/doing/paused) from the archive."""
        tasks = TaskManager(agent_id, self._paths)
        tasks.load()
        return [t.title for t in tasks.list_tasks() if t.status != TaskStatus.DONE]

    async def _open_threads(self, agent_id: str) -> list[dict[str, str]]:
        """Unreplied inbox threads: the thread's last inbox mail is from
        someone else and the retiree's sent file holds no later reply in
        that thread (the mirror image of the orphan-mail scan, which
        finds "I wrote and nobody answered")."""
        mail_dir = self._paths.member_mail_dir(agent_id)
        if not mail_dir.is_dir():
            return []
        mailbox = Mailbox(mail_dir)
        own_address = address_for_member(agent_id)

        # File order is chronological: the last write wins per thread.
        last_inbox: dict[str, Mail] = {}
        for mail in await mailbox.inbox():
            last_inbox[mail.thread_id] = mail

        last_sent: dict[str, datetime] = {}
        for mail in await mailbox.sent():
            ts = _parse_timestamp(mail.timestamp)
            if ts is None:
                continue
            previous = last_sent.get(mail.thread_id)
            if previous is None or ts > previous:
                last_sent[mail.thread_id] = ts

        open_threads: list[dict[str, str]] = []
        for thread_id, mail in last_inbox.items():
            if mail.from_address == own_address:
                continue  # own last word is not "awaiting my reply"
            ts = _parse_timestamp(mail.timestamp)
            if ts is not None and last_sent.get(thread_id, datetime.min.replace(tzinfo=UTC)) > ts:
                continue  # already answered
            open_threads.append(
                {
                    "thread_id": thread_id,
                    "subject": mail.subject or "(无主题)",
                    "from": mail.from_address,
                }
            )
        return open_threads

    async def _memory_digest(self, agent_id: str) -> dict[str, Any] | None:
        """The archived memory digest for cognitive members; None for
        non-cognitive ones (no memory worth reading)."""
        if not self._is_cognitive(agent_id):
            return None
        memory = MemoryManager(
            agent_id,
            MemoryConfig(),
            data_dir=self._paths.agent_dir(agent_id),
            tlon_paths=self._paths,
        )
        journal = SessionJournal(agent_id, self._paths)
        return await memory.get_memory_digest(journal)

    # ── Farewell letter ──

    @staticmethod
    def _recipient_address(spawned_by: str | None) -> str:
        """The handover addressee: the spawner member when there is one,
        else the guest human (never adam@ — undeliverable until )."""
        if spawned_by:
            return address_for_member(spawned_by)
        return address_for_member(GUEST_HUMAN_ID)

    async def _send_farewell(
        self,
        agent_id: str,
        name: str,
        to: str,
        open_tasks: list[str],
        open_threads: list[dict[str, str]],
        digest: dict[str, Any] | None,
    ) -> bool:
        """One attributed LLM pass + one MAIL_NEW; False on any failure."""
        try:
            client = self._client_factory(agent_id)
            response = await client.chat(
                system_prompt=FAREWELL_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": self._build_prompt(name, open_tasks, open_threads, digest),
                    }
                ],
                timeout=60.0,
                purpose="farewell",
            )
            if not response.content:
                logger.info(
                    "retirement_farewell_skipped", agent_id=agent_id, reason="empty_response"
                )
                return False
            mail = Mail.create(
                from_address=address_for_member(agent_id),
                to=[to],
                subject=f"告别与交接：{name}",
                content=response.content,
            )
            await self.publish(Event(type=EventType.MAIL_NEW, payload=mail, source=agent_id))
            logger.info("retirement_farewell_sent", agent_id=agent_id, to=to, mail_id=mail.id)
            return True
        except Exception as e:
            logger.warning("retirement_farewell_failed", agent_id=agent_id, error=str(e))
            return False

    @staticmethod
    def _build_prompt(
        name: str,
        open_tasks: list[str],
        open_threads: list[dict[str, str]],
        digest: dict[str, Any] | None,
    ) -> str:
        """Render the handover inventory as the letter's raw material."""
        parts = [f"退休成员：{name}\n"]

        if digest is not None:
            parts.append("## 记忆")
            parts.append(f"记忆数：{digest['claim_count']}")
            for c in digest["claims"]:
                cid = str(c.get("id", ""))[:8]
                date = str(c.get("timestamp", ""))[:10]
                parts.append(f"- [{cid}] [{date}] {c.get('text', '')}")
            parts.append("")

        parts.append("## 未完成任务")
        if open_tasks:
            parts.extend(f"- {title}" for title in open_tasks)
        else:
            parts.append("（无）")
        parts.append("")

        parts.append("## 未回复的邮件线程")
        if open_threads:
            parts.extend(f"- 「{t['subject']}」（来自 {t['from']}）" for t in open_threads)
        else:
            parts.append("（无）")
        parts.append("")

        parts.append("请基于以上材料写告别信。")
        return "\n".join(parts)
