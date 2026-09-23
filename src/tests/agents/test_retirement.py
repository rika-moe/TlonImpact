"""RetirementCeremony — farewell letter and task handover (issue ).

Pins the closed loop: publishing AGENT_STOPPED for a member with
unfinished business takes the offline inventory (unfinished tasks,
unreplied threads, memory digest), mails one farewell letter from the
retired member's own address to its spawner (or the guest human), and
always publishes the AGENT_RETIRED fact — empty handover skips the LLM,
an LLM failure is logged and never raised, and the event goes out either
way.
"""

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tlon.agents.retirement import RetirementCeremony
from tlon.base.config import AgentConfig
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMResponse
from tlon.base.paths import TlonPaths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.organization.mail.delivery import MailDelivery
from tlon.organization.mail.mail import Mail, address_for_member
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.messaging.human_adapter import AdapterManager
from tlon.tools.tasks.manager import TaskManager

_COGNITIVE_SPEC = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions []
          :loop {:context {:sections [(persona)]}
                 :stages [(think)]
                 :until (iteration-limit-reached?)
                 :max-iterations 1
                 :max-consecutive-errors 1
                 :reflect {:enabled false :interval-minutes 15}}}]}
"""

_DETERMINISTIC_SPEC = """\
{:version 1
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["x@tlon.internal"]})]}]}
"""


class _FakeLLM:
    """Duck-typed LLMClient returning one canned response."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        return LLMResponse(content=self._content)


class _FailingLLM:
    """Duck-typed LLMClient whose chat always raises."""

    async def chat(self, **kwargs: Any) -> LLMResponse:
        raise RuntimeError("provider down")


class _StaticAgentInfo:
    """Minimal AgentInfoProvider for the delivery path."""

    def __init__(self, configs: dict[str, AgentConfig]) -> None:
        self._configs = configs

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)

    def get_all_ids(self) -> set[str]:
        return set(self._configs)


def _write_behavior(p: TlonPaths, agent_id: str, spec: str) -> None:
    behavior_file = p.agent_behavior_file(agent_id)
    behavior_file.parent.mkdir(parents=True, exist_ok=True)
    behavior_file.write_text(spec, encoding="utf-8")


def _write_config(p: TlonPaths, agent_id: str, name: str, spawned_by: str | None = None) -> None:
    lines = ["agent:", f"  id: {agent_id}", f"  name: {name}"]
    if spawned_by is not None:
        lines.append(f"  spawned_by: {spawned_by}")
    p.agent_config_file(agent_id).write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _remember(p: TlonPaths, agent_id: str, fact: str) -> None:
    manager = MemoryManager(agent_id, MemoryConfig(), data_dir=p.agent_dir(agent_id))
    await manager.remember_fact(fact)


def _mail(
    mail_id: str,
    from_address: str,
    to: str,
    thread_id: str,
    timestamp: str,
    subject: str | None = None,
) -> Mail:
    return Mail(
        id=mail_id,
        from_address=from_address,
        to=[to],
        cc=[],
        subject=subject,
        content="正文",
        in_reply_to=None,
        thread_id=thread_id,
        forward_of=None,
        timestamp=timestamp,
    )


def _stopped_event(agent_id: str) -> Event:
    return Event(
        type=EventType.AGENT_STOPPED,
        payload={"agent_id": agent_id, "stopped_by": "adam"},
        source="agent_registry",
    )


async def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


def _make_ceremony(
    bus: EventBus, p: TlonPaths, llm: Any
) -> tuple[RetirementCeremony, list[Event]]:
    """A ceremony on the bus plus a capture list for AGENT_RETIRED."""
    retired: list[Event] = []
    bus.subscribe(EventType.AGENT_RETIRED, lambda event: retired.append(event))
    ceremony = RetirementCeremony(
        event_bus=bus, tlon_paths=p, llm_client_factory=lambda _id: llm
    )
    return ceremony, retired


async def test_removed_member_skips_ceremony(tmp_path: Path) -> None:
    """AGENT_STOPPED with removed=True (administrative removal) skips the
    whole ceremony: no archive resurrection on disk, no AGENT_RETIRED."""
    p = TlonPaths(home=tmp_path)
    bus = EventBus()
    await bus.initialize()
    llm = _FakeLLM("不该被调用")
    ceremony, retired = _make_ceremony(bus, p, llm)
    try:
        await ceremony._on_agent_stopped(
            Event(
                type=EventType.AGENT_STOPPED,
                payload={"agent_id": "gone", "stopped_by": "admin-api", "removed": True},
                source="agent_registry",
            )
        )

        await asyncio.sleep(0.05)  # give any misfired handler a chance
        assert not (tmp_path / "gone").exists()
        assert retired == []
        assert llm.calls == []
    finally:
        await bus.shutdown()


async def test_farewell_letter_delivered_to_spawner(tmp_path: Path) -> None:
    """Full bus path: AGENT_STOPPED -> inventory -> farewell letter lands
    in the spawner's inbox + AGENT_RETIRED with the handover payload."""
    p = TlonPaths(home=tmp_path)
    _write_behavior(p, "retiree", _COGNITIVE_SPEC)
    _write_config(p, "retiree", name="老张", spawned_by="spawner-1")
    await _remember(p, "retiree", "部署前先跑质量门")
    tasks = TaskManager("retiree", p)
    tasks.create_task("写完退休文档")
    box = Mailbox(p.member_mail_dir("retiree"))
    await box.append_inbox(
        _mail("m1", "gu-yanxi@tlon.internal", "retiree@tlon.internal", "t1",
              "2026-09-01T10:00:00", subject="评审提案")
    )

    bus = EventBus()
    await bus.initialize()
    provider = _StaticAgentInfo(
        {
            "retiree": AgentConfig(id="retiree", name="老张"),
            "spawner-1": AgentConfig(id="spawner-1", name="继任者"),
        }
    )
    adapter_manager = AdapterManager(event_bus=bus, agent_info_provider=provider, tlon_paths=p)
    delivery = MailDelivery(bus, provider, adapter_manager, tlon_paths=p)
    await delivery.initialize()
    llm = _FakeLLM("# 告别信\n各位再见。")
    ceremony, retired = _make_ceremony(bus, p, llm)
    await ceremony.initialize()
    try:
        await bus.publish(_stopped_event("retiree"))

        spawner_inbox = p.member_mail_dir("spawner-1") / "inbox.jsonl"
        assert await _wait_for(spawner_inbox.exists)
        assert await _wait_for(lambda: len(retired) == 1)

        letter = (await Mailbox(p.member_mail_dir("spawner-1")).inbox())[0]
        assert letter.from_address == "retiree@tlon.internal"
        assert letter.content == "# 告别信\n各位再见。"
        assert letter.subject == "告别与交接：老张"

        payload = retired[0].payload
        assert payload["agent_id"] == "retiree"
        assert payload["name"] == "老张"
        assert payload["stopped_by"] == "adam"
        assert payload["open_tasks"] == ["写完退休文档"]
        assert payload["open_threads"] == ["评审提案"]
        assert payload["farewell_sent"] is True
        assert payload["farewell_to"] == "spawner-1@tlon.internal"

        assert llm.calls[0]["purpose"] == "farewell"
        assert "temperature" not in llm.calls[0]
        assert llm.calls[0]["timeout"] == 60.0
    finally:
        await ceremony.shutdown()
        await delivery.shutdown()
        await bus.shutdown()


async def test_farewell_falls_back_to_guest_human(tmp_path: Path) -> None:
    """No spawned_by in the archive -> the letter goes to the guest human."""
    p = TlonPaths(home=tmp_path)
    _write_behavior(p, "retiree", _COGNITIVE_SPEC)
    _write_config(p, "retiree", name="老王")
    await _remember(p, "retiree", "一条事实")
    p.member_mail_dir("human:guest").mkdir(parents=True, exist_ok=True)  # known human

    bus = EventBus()
    await bus.initialize()
    provider = _StaticAgentInfo({"retiree": AgentConfig(id="retiree", name="老王")})
    adapter_manager = AdapterManager(event_bus=bus, agent_info_provider=provider, tlon_paths=p)
    delivery = MailDelivery(bus, provider, adapter_manager, tlon_paths=p)
    await delivery.initialize()
    ceremony, retired = _make_ceremony(bus, p, _FakeLLM("告别。"))
    await ceremony.initialize()
    try:
        await bus.publish(_stopped_event("retiree"))

        guest_inbox = p.member_mail_dir("human:guest") / "inbox.jsonl"
        assert await _wait_for(guest_inbox.exists)
        assert await _wait_for(lambda: len(retired) == 1)
        assert retired[0].payload["farewell_sent"] is True
        assert retired[0].payload["farewell_to"] == address_for_member("human:guest")
    finally:
        await ceremony.shutdown()
        await delivery.shutdown()
        await bus.shutdown()


async def test_open_tasks_inventory(tmp_path: Path) -> None:
    """Only todo/doing/paused titles are inventoried; done is excluded."""
    p = TlonPaths(home=tmp_path)
    tasks = TaskManager("retiree", p)
    tasks.create_task("待办事项")
    paused = tasks.create_task("暂停事项")
    tasks.start_task(paused.id)
    tasks.pause_task(paused.id)
    doing = tasks.create_task("进行中事项")
    tasks.start_task(doing.id)
    done = tasks.create_task("已完成事项")
    tasks.complete_task(done.id)

    bus = EventBus()
    ceremony, _ = _make_ceremony(bus, p, _FakeLLM("x"))

    assert sorted(ceremony._open_tasks("retiree")) == ["待办事项", "暂停事项", "进行中事项"]


async def test_open_threads_unreplied_counted(tmp_path: Path) -> None:
    p = TlonPaths(home=tmp_path)
    box = Mailbox(p.member_mail_dir("retiree"))
    await box.append_inbox(
        _mail("m1", "gu-yanxi@tlon.internal", "retiree@tlon.internal", "t1",
              "2026-09-01T10:00:00", subject="未回复的事")
    )

    ceremony, _ = _make_ceremony(EventBus(), p, _FakeLLM("x"))
    threads = await ceremony._open_threads("retiree")

    assert [t["subject"] for t in threads] == ["未回复的事"]
    assert threads[0]["from"] == "gu-yanxi@tlon.internal"


async def test_open_threads_answered_excluded(tmp_path: Path) -> None:
    """A thread with a later own reply in sent is not awaiting me."""
    p = TlonPaths(home=tmp_path)
    own = address_for_member("retiree")
    box = Mailbox(p.member_mail_dir("retiree"))
    await box.append_inbox(
        _mail("m1", "gu-yanxi@tlon.internal", own, "t1",
              "2026-09-01T10:00:00", subject="已回复的事")
    )
    await box.append_sent(
        _mail("m2", own, "gu-yanxi@tlon.internal", "t1", "2026-09-01T11:00:00")
    )

    ceremony, _ = _make_ceremony(EventBus(), p, _FakeLLM("x"))

    assert await ceremony._open_threads("retiree") == []


async def test_open_threads_newer_incoming_reopens(tmp_path: Path) -> None:
    """My reply is not the last word when a newer incoming mail follows."""
    p = TlonPaths(home=tmp_path)
    own = address_for_member("retiree")
    box = Mailbox(p.member_mail_dir("retiree"))
    await box.append_inbox(
        _mail("m1", "gu-yanxi@tlon.internal", own, "t1", "2026-09-01T10:00:00")
    )
    await box.append_sent(
        _mail("m2", own, "gu-yanxi@tlon.internal", "t1", "2026-09-01T11:00:00")
    )
    await box.append_inbox(
        _mail("m3", "gu-yanxi@tlon.internal", own, "t1",
              "2026-09-01T12:00:00", subject="追问")
    )

    ceremony, _ = _make_ceremony(EventBus(), p, _FakeLLM("x"))
    threads = await ceremony._open_threads("retiree")

    assert [t["subject"] for t in threads] == ["追问"]


async def test_open_threads_no_mailbox(tmp_path: Path) -> None:
    ceremony, _ = _make_ceremony(EventBus(), TlonPaths(home=tmp_path), _FakeLLM("x"))

    assert await ceremony._open_threads("ghost") == []


async def test_empty_handover_skips_llm_but_publishes_event(tmp_path: Path) -> None:
    """Nothing to hand over -> no LLM call, farewell_sent=false, event out."""
    p = TlonPaths(home=tmp_path)
    _write_behavior(p, "fresh", _COGNITIVE_SPEC)
    bus = EventBus()
    await bus.initialize()
    llm = _FakeLLM("不该被调用")
    ceremony, retired = _make_ceremony(bus, p, llm)
    try:
        await ceremony._on_agent_stopped(_stopped_event("fresh"))

        assert await _wait_for(lambda: len(retired) == 1)
        assert llm.calls == []
        payload = retired[0].payload
        assert payload["open_tasks"] == []
        assert payload["open_threads"] == []
        assert payload["farewell_sent"] is False
        assert payload["farewell_to"] is None
        assert payload["name"] == "fresh"  # no agent.yaml: id is the fallback
    finally:
        await bus.shutdown()


async def test_non_cognitive_with_open_tasks_still_writes(tmp_path: Path) -> None:
    """A non-cognitive member with unfinished business still gets a letter
    (only 'nothing to hand over' skips it)."""
    p = TlonPaths(home=tmp_path)
    _write_behavior(p, "relay-1", _DETERMINISTIC_SPEC)
    tasks = TaskManager("relay-1", p)
    tasks.create_task("还没转完的信")
    bus = EventBus()
    await bus.initialize()
    llm = _FakeLLM("交接：还有一封信没转完。")
    ceremony, retired = _make_ceremony(bus, p, llm)
    try:
        await ceremony._on_agent_stopped(_stopped_event("relay-1"))

        assert await _wait_for(lambda: len(retired) == 1)
        assert len(llm.calls) == 1
        payload = retired[0].payload
        assert payload["open_tasks"] == ["还没转完的信"]
        assert payload["farewell_sent"] is True
        assert payload["farewell_to"] == address_for_member("human:guest")
    finally:
        await bus.shutdown()


async def test_llm_failure_logged_never_raised_event_published(tmp_path: Path) -> None:
    p = TlonPaths(home=tmp_path)
    _write_behavior(p, "retiree", _COGNITIVE_SPEC)
    await _remember(p, "retiree", "一条事实")
    bus = EventBus()
    await bus.initialize()
    ceremony, retired = _make_ceremony(bus, p, _FailingLLM())
    try:
        await ceremony._on_agent_stopped(_stopped_event("retiree"))  # must not raise

        assert await _wait_for(lambda: len(retired) == 1)
        payload = retired[0].payload
        assert payload["farewell_sent"] is False
        assert payload["farewell_to"] is None
    finally:
        await bus.shutdown()
