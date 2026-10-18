"""Permanent exit only inventories archives; no LLM farewell or outgoing mail."""

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tlon.agents.retirement import RetirementCeremony
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMClient
from tlon.base.paths import TlonPaths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.organization.mail.mail import Mail, address_for_member
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.post_office import PostOffice
from tlon.organization.mail.store import MailStore
from tlon.tools.tasks.manager import TaskManager

_SESSION_PROGRAM = '{:version 1 :rules [{:name "respond" :on :mail-delivered :actions [(run-loop (quote {:context {:sections [(persona)]} :stages [(think)] :until (iteration-limit-reached?) :max-iterations 1 :max-consecutive-errors 1 :max-tokens 1000000 :reflect {:enabled false :interval-minutes 15}}))]}]}'

_RELAY_PROGRAM = """\
{:version 1
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["x@tlon.internal"]})]}]}
"""


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


def _make_ceremony(bus: EventBus, paths: TlonPaths) -> tuple[RetirementCeremony, list[Event]]:
    retired: list[Event] = []
    bus.subscribe(EventType.AGENT_RETIRED, lambda event: retired.append(event))
    return RetirementCeremony(event_bus=bus, tlon_paths=paths), retired


async def test_dead_member_inventory_preserves_archive_without_llm_or_mail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = TlonPaths(home=tmp_path)
    _write_behavior(paths, "retiree", _SESSION_PROGRAM)
    _write_config(paths, "retiree", "老张", "spawner-1")
    await _remember(paths, "retiree", "部署前先跑质量门")
    tasks = TaskManager("retiree", paths)
    tasks.create_task("写完文档")
    box = Mailbox(paths.mail_database_file(), "retiree")
    incoming = _mail(
        "m1",
        "other@tlon.internal",
        "retiree@tlon.internal",
        "t1",
        "2026-09-01T10:00:00+00:00",
        "评审提案",
    )
    await box.append_inbox(incoming)
    MailStore(paths.mail_database_file()).retire("retiree")
    chat = AsyncMock(side_effect=AssertionError("dead member must not call LLM"))
    post = AsyncMock(side_effect=AssertionError("dead member must not send mail"))
    monkeypatch.setattr(LLMClient, "chat", chat)
    monkeypatch.setattr(PostOffice, "post", post)
    bus = EventBus()
    await bus.initialize()
    ceremony, retired = _make_ceremony(bus, paths)
    await ceremony.initialize()
    try:
        await bus.publish(_stopped_event("retiree"))
        assert await _wait_for(lambda: len(retired) == 1)
        payload = retired[0].payload
        assert payload["agent_id"] == "retiree"
        assert payload["name"] == "老张"
        assert payload["stopped_by"] == "adam"
        assert payload["open_tasks"] == ["写完文档"]
        assert payload["open_threads"] == [
            {
                "thread_id": "t1",
                "subject": "评审提案",
                "from": "other@tlon.internal",
            }
        ]
        assert payload["memory_digest"]["claim_count"] == 1
        assert set(payload) == {
            "agent_id",
            "name",
            "stopped_by",
            "open_tasks",
            "open_threads",
            "memory_digest",
        }
        chat.assert_not_called()
        post.assert_not_called()
        assert await box.inbox() == [incoming]
        assert await box.sent() == []
        assert tasks.list_tasks()[0].title == "写完文档"
        assert paths.agent_config_file("retiree").exists()
    finally:
        await ceremony.shutdown()
        await bus.shutdown()


async def test_removed_member_skips_inventory(tmp_path: Path) -> None:
    bus = EventBus()
    await bus.initialize()
    ceremony, retired = _make_ceremony(bus, TlonPaths(home=tmp_path))
    try:
        await ceremony._on_agent_stopped(
            Event(
                type=EventType.AGENT_STOPPED,
                payload={"agent_id": "gone", "removed": True},
                source="registry",
            )
        )
        assert retired == []
        assert not (tmp_path / "gone").exists()
    finally:
        await bus.shutdown()


@pytest.mark.parametrize("spec", [_SESSION_PROGRAM, _RELAY_PROGRAM])
async def test_empty_inventory_is_still_published(tmp_path: Path, spec: str) -> None:
    paths = TlonPaths(home=tmp_path)
    _write_behavior(paths, "fresh", spec)
    bus = EventBus()
    await bus.initialize()
    ceremony, retired = _make_ceremony(bus, paths)
    try:
        await ceremony._on_agent_stopped(_stopped_event("fresh"))
        assert await _wait_for(lambda: len(retired) == 1)
        payload = retired[0].payload
        assert payload["open_tasks"] == []
        assert payload["open_threads"] == []
        assert payload["name"] == "fresh"
        assert not any(key.startswith("farewell") for key in payload)
    finally:
        await bus.shutdown()


async def test_relay_inventory_includes_unfinished_tasks(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    _write_behavior(paths, "relay-1", _RELAY_PROGRAM)
    tasks = TaskManager("relay-1", paths)
    tasks.create_task("还没转完的信")
    bus = EventBus()
    await bus.initialize()
    ceremony, retired = _make_ceremony(bus, paths)
    try:
        await ceremony._on_agent_stopped(_stopped_event("relay-1"))
        assert await _wait_for(lambda: len(retired) == 1)
        assert retired[0].payload["open_tasks"] == ["还没转完的信"]
        assert retired[0].payload["memory_digest"] is None
    finally:
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
    ceremony, _ = _make_ceremony(bus, p)

    assert sorted(ceremony._open_tasks("retiree")) == ["待办事项", "暂停事项", "进行中事项"]


async def test_open_threads_unreplied_counted(tmp_path: Path) -> None:
    p = TlonPaths(home=tmp_path)
    box = Mailbox(p.mail_database_file(), "retiree")
    await box.append_inbox(
        _mail(
            "m1",
            "gu-yanxi@tlon.internal",
            "retiree@tlon.internal",
            "t1",
            "2026-09-01T10:00:00",
            subject="未回复的事",
        )
    )

    ceremony, _ = _make_ceremony(EventBus(), p)
    threads = await ceremony._open_threads("retiree")

    assert [t["subject"] for t in threads] == ["未回复的事"]
    assert threads[0]["from"] == "gu-yanxi@tlon.internal"


async def test_open_threads_answered_excluded(tmp_path: Path) -> None:
    """A thread with a later own reply in sent is not awaiting me."""
    p = TlonPaths(home=tmp_path)
    own = address_for_member("retiree")
    box = Mailbox(p.mail_database_file(), "retiree")
    await box.append_inbox(
        _mail(
            "m1", "gu-yanxi@tlon.internal", own, "t1", "2026-09-01T10:00:00", subject="已回复的事"
        )
    )
    await box.append_sent(_mail("m2", own, "gu-yanxi@tlon.internal", "t1", "2026-09-01T11:00:00"))

    ceremony, _ = _make_ceremony(EventBus(), p)

    assert await ceremony._open_threads("retiree") == []


async def test_open_threads_newer_incoming_reopens(tmp_path: Path) -> None:
    """My reply is not the last word when a newer incoming mail follows."""
    p = TlonPaths(home=tmp_path)
    own = address_for_member("retiree")
    box = Mailbox(p.mail_database_file(), "retiree")
    await box.append_inbox(_mail("m1", "gu-yanxi@tlon.internal", own, "t1", "2026-09-01T10:00:00"))
    await box.append_sent(_mail("m2", own, "gu-yanxi@tlon.internal", "t1", "2026-09-01T11:00:00"))
    await box.append_inbox(
        _mail("m3", "gu-yanxi@tlon.internal", own, "t1", "2026-09-01T12:00:00", subject="追问")
    )

    ceremony, _ = _make_ceremony(EventBus(), p)
    threads = await ceremony._open_threads("retiree")

    assert [t["subject"] for t in threads] == ["追问"]


async def test_open_threads_no_mailbox(tmp_path: Path) -> None:
    ceremony, _ = _make_ceremony(EventBus(), TlonPaths(home=tmp_path))

    assert await ceremony._open_threads("ghost") == []
