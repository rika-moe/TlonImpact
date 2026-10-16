"""ContextBuilder — the ``(current-task)`` section (ADR 0008), the
``(thread-recap)`` section (redefined by ADR 0028: the member's latest
completed session), the two-tier ``(mail-history F R)`` (ADR 0025),
``(lessons)``, and the ``(knowledge)`` JIT query (ADR 0017/0028).

ADR 0028: there is no trigger mail. ``build_context`` takes the wake
trigger text (the fixed "你有 N 封未读邮件" template — always the last
message, the engine owns the tail) plus the responsibility set (which
feeds the JIT knowledge query and nothing else). The thread-recap
section is thread-agnostic now: it recaps the member's most recent
completed session, rebuilt deterministically from its journal (ADR 0007).
"""

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tlon.agents.context_builder import ContextBuilder, _render_thread_recap
from tlon.agents.member_spec import (
    MemberRule,
    MemberSpec,
    MemberSpecValidationError,
    validate_member_spec,
)
from tlon.agents.session_journal import SessionJournal
from tlon.base.config import AgentConfig
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.tools.tasks.manager import TaskManager
from tlosure.edn import loads

AGENT = "agent1"
ADDRESS = f"{AGENT}@tlon.internal"

#: The wake trigger text used across these tests (ADR 0028: fixed
#: template, always the last context message).
TRIGGER = "你有 1 封未读邮件"


class _MemoryStub:
    """Duck-typed MemoryManager; the sections under test never touch it."""


def _rule_with_sections(sections: str) -> MemberRule:
    spec = MemberSpec.from_edn(
        loads(
            """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions []
          :loop {:context {:sections SECTIONS}
                 :stages [(think)]
                 :until (iteration-limit-reached?)
                 :max-iterations 1
                 :max-consecutive-errors 1
                 :max-tokens 1000000
                 :reflect {:enabled false :interval-minutes 15}}}]}
""".replace("SECTIONS", sections)
        )
    )
    rule = spec.rules[0]
    assert rule.loop is not None
    return rule


def _make_builder(tmp_path: Path, sections: str) -> tuple[ContextBuilder, TaskManager]:
    task_manager = TaskManager(agent_id=AGENT, tlon_paths=TlonPaths(home=tmp_path))
    builder = ContextBuilder(
        AgentConfig(id=AGENT, name=AGENT),
        None,
        _MemoryStub(),  # type: ignore[arg-type]
        task_manager,
        _rule_with_sections(sections),
        ADDRESS,
        tlon_paths=TlonPaths(home=tmp_path),
    )
    return builder, task_manager


def _make_thread_builder(
    tmp_path: Path, sections: str
) -> tuple[ContextBuilder, Mailbox, SessionJournal]:
    """A builder wired to a real mailbox and journal over ``tmp_path``."""
    tlon_paths = TlonPaths(home=tmp_path)
    mailbox = Mailbox(tmp_path / "mail.sqlite3", AGENT)
    journal = SessionJournal(AGENT, tlon_paths)
    builder = ContextBuilder(
        AgentConfig(id=AGENT, name=AGENT),
        mailbox,
        _MemoryStub(),  # type: ignore[arg-type]
        TaskManager(agent_id=AGENT, tlon_paths=tlon_paths),
        _rule_with_sections(sections),
        ADDRESS,
        journal,
        tlon_paths=tlon_paths,
    )
    return builder, mailbox, journal


async def _write_completed_session(
    journal: SessionJournal,
    tlon: TlonPaths,
    *,
    trigger: str,
    conclusion: str,
    tool_text: str | None = None,
    mtime: float | None = None,
) -> str:
    """Journal one completed session (trigger -> tool -> reply -> end);
    returns the session id. ``mtime`` pins the log's mtime (the recap's
    latest-completed ordering key)."""
    sid = f"{AGENT}-s-{trigger[:8]}-{mtime or 0}"
    await journal.append(
        sid,
        "session/start",
        agent_id=AGENT,
        trigger_content=trigger,
        carrier=None,
        responsibility=[],
        context=[],
    )
    if tool_text is not None:
        await journal.append(
            sid,
            "tool/result",
            tool_call={
                "id": "tc1",
                "type": "function",
                "function": {"name": "readFile", "arguments": "{}"},
            },
            result={"success": True, "data": {}, "message": tool_text, "error": None},
        )
    await journal.append(sid, "llm/response", response={"content": conclusion})
    await journal.append(sid, "session/end", reason="complete")
    if mtime is not None:
        os.utime(tlon.agent_sessions_dir(AGENT) / f"{sid}.jsonl", (mtime, mtime))
    return sid


def _mail(content: str = "hello", subject: str | None = None) -> Mail:
    return Mail.create(
        from_address="alice@tlon.internal", content=content, to=[ADDRESS], subject=subject
    )


async def _build(
    builder: ContextBuilder, responsibility: list[Mail] | None = None
) -> list[dict[str, Any]]:
    """build_context with the test wake trigger and a responsibility set."""
    return (await builder.build_context(TRIGGER, responsibility or [_mail()])).messages


# ===== DSL section =====


def test_current_task_section_accepted() -> None:
    spec = MemberSpec.from_edn(
        loads(
            """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions []
          :loop {:context {:sections [(persona) (current-task) (mail-history 5)]}
                 :stages [(think)]
                 :until (iteration-limit-reached?)
                 :max-iterations 1
                 :max-consecutive-errors 1
                 :max-tokens 1000000
                 :reflect {:enabled false :interval-minutes 15}}}]}
"""
        )
    )
    validate_member_spec(spec)


def test_current_task_section_rejects_arguments() -> None:
    rule_spec = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions []
          :loop {:context {:sections [(current-task 3)]}
                 :stages [(think)]
                 :until (iteration-limit-reached?)
                 :max-iterations 1
                 :max-consecutive-errors 1
                 :max-tokens 1000000
                 :reflect {:enabled false :interval-minutes 15}}}]}
"""
    spec = MemberSpec.from_edn(loads(rule_spec))
    with pytest.raises(MemberSpecValidationError, match="takes no arguments"):
        validate_member_spec(spec)


# ===== build_context =====


async def test_doing_task_injected(tmp_path: Path) -> None:
    builder, tasks = _make_builder(tmp_path, "[(current-task)]")
    task = tasks.create_task("写周报", "汇总本周进展")
    tasks.start_task(task.id)

    context = await _build(builder)

    assert len(context) == 2  # section message + wake trigger
    section = context[0]
    assert section["role"] == "user"
    assert section["content"].startswith("[当前任务]")
    assert "t-001: 写周报（doing）" in section["content"]
    assert "描述：汇总本周进展" in section["content"]
    assert context[1]["content"] == TRIGGER


async def test_no_task_injects_nothing(tmp_path: Path) -> None:
    builder, _ = _make_builder(tmp_path, "[(current-task)]")

    context = await _build(builder)

    assert len(context) == 1  # only the wake trigger
    assert context[0]["content"] == TRIGGER


async def test_todo_task_not_injected(tmp_path: Path) -> None:
    builder, tasks = _make_builder(tmp_path, "[(current-task)]")
    tasks.create_task("还没开始的任务")

    context = await _build(builder)

    assert len(context) == 1


async def test_paused_task_not_injected(tmp_path: Path) -> None:
    """A paused task is not the current task — no get_current_task fallback."""
    builder, tasks = _make_builder(tmp_path, "[(current-task)]")
    task = tasks.create_task("暂停的任务")
    tasks.start_task(task.id)
    tasks.pause_task(task.id)

    context = await _build(builder)

    assert len(context) == 1


async def test_undeclared_section_injects_nothing(tmp_path: Path) -> None:
    """A spec without (current-task) never sees the task, doing or not."""
    builder, tasks = _make_builder(tmp_path, "[(persona)]")
    task = tasks.create_task("进行中的任务")
    tasks.start_task(task.id)

    context = await _build(builder)

    assert len(context) == 1
    assert context[0]["content"] == TRIGGER


# ===== contact book injection (ADR 0024) =====


async def test_system_prompt_injects_own_contact_book(tmp_path: Path) -> None:
    """The team-wide SOUL roster is gone: the prompt carries the member's
    own CONTACTS.md (in a 联系簿 section), never another member's SOUL."""
    tlon_paths = TlonPaths(home=tmp_path)
    contacts = tlon_paths.agent_contacts_file(AGENT)
    contacts.parent.mkdir(parents=True, exist_ok=True)
    contacts.write_text(
        "# 联系簿\n\n## 林知遥\n- 地址：lin@tlon.internal\n- 描述：聪明\n", encoding="utf-8"
    )
    # Another member's SOUL must not leak into this member's prompt.
    other_soul = tlon_paths.agent_soul_file("someone-else")
    other_soul.parent.mkdir(parents=True, exist_ok=True)
    other_soul.write_text("别人的人设自述", encoding="utf-8")
    builder, _ = _make_builder(tmp_path, "[(persona)]")

    prompt = await builder.get_system_prompt()

    assert "## 联系簿" in prompt
    assert "林知遥" in prompt
    assert "别人的人设自述" not in prompt
    assert "## 团队成员" not in prompt


async def test_empty_contact_book_gets_empty_state_section(tmp_path: Path) -> None:
    """缺省即空：no CONTACTS.md still renders the section's empty state."""
    builder, _ = _make_builder(tmp_path, "[(persona)]")

    prompt = await builder.get_system_prompt()

    assert "## 联系簿" in prompt
    assert "你还不认识任何人" in prompt


async def test_contact_book_edit_rebuilds_prompt_cache(tmp_path: Path) -> None:
    """The mtime signature covers CONTACTS.md — an updateContact write
    takes effect at the next assembly (no restart)."""
    tlon_paths = TlonPaths(home=tmp_path)
    builder, _ = _make_builder(tmp_path, "[(persona)]")
    assert "林知遥" not in await builder.get_system_prompt()

    contacts = tlon_paths.agent_contacts_file(AGENT)
    contacts.parent.mkdir(parents=True, exist_ok=True)
    contacts.write_text("## 林知遥\n- 地址：lin@tlon.internal\n- 描述：聪明\n", encoding="utf-8")

    assert "林知遥" in await builder.get_system_prompt()


def test_persona_files_are_all_own_files(tmp_path: Path) -> None:
    """The mtime signature covers exactly the member's own files — no
    other member's SOUL (a teammate editing their SOUL must not rebuild
    this member's prompt cache)."""
    tlon_paths = TlonPaths(home=tmp_path)
    builder, _ = _make_builder(tmp_path, "[(persona)]")

    files = builder.persona_files()

    assert files == [
        tlon_paths.agent_identity_file(AGENT),
        tlon_paths.agent_soul_file(AGENT),
        tlon_paths.agent_rules_file(AGENT),
        tlon_paths.agent_contacts_file(AGENT),
    ]


async def test_section_follows_vector_order(tmp_path: Path) -> None:
    """The section lands where the vector puts it — before mail-history here."""
    builder, tasks = _make_builder(tmp_path, "[(current-task) (mail-history 5)]")
    task = tasks.create_task("排序任务")
    tasks.start_task(task.id)

    context = await _build(builder)

    # No mailbox history (mailbox=None degrades to empty): task, then trigger.
    assert context[0]["content"].startswith("[当前任务]")
    assert context[1]["content"] == TRIGGER


# ===== (thread-recap) section — ADR 0028: the member's latest completed session =====


def test_thread_recap_section_accepted() -> None:
    spec = MemberSpec.from_edn(
        loads(
            """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions []
          :loop {:context {:sections [(thread-recap) (mail-history 5)]}
                 :stages [(think)]
                 :until (iteration-limit-reached?)
                 :max-iterations 1
                 :max-consecutive-errors 1
                 :max-tokens 1000000
                 :reflect {:enabled false :interval-minutes 15}}}]}
"""
        )
    )
    validate_member_spec(spec)


def test_thread_recap_section_rejects_arguments() -> None:
    rule_spec = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions []
          :loop {:context {:sections [(thread-recap 3)]}
                 :stages [(think)]
                 :until (iteration-limit-reached?)
                 :max-iterations 1
                 :max-consecutive-errors 1
                 :max-tokens 1000000
                 :reflect {:enabled false :interval-minutes 15}}}]}
"""
    spec = MemberSpec.from_edn(loads(rule_spec))
    with pytest.raises(MemberSpecValidationError, match="takes no arguments"):
        validate_member_spec(spec)


async def test_no_completed_session_injects_nothing(tmp_path: Path) -> None:
    """The member's first session: no previous completed session — the
    section is skipped."""
    builder, _mailbox, _journal = _make_thread_builder(tmp_path, "[(thread-recap)]")

    context = await _build(builder)

    assert len(context) == 1  # only the wake trigger
    assert context[0]["content"] == TRIGGER


async def test_latest_completed_session_is_recapped(tmp_path: Path) -> None:
    """The recap shows what the member's most recent completed session did
    (thread-agnostic, ADR 0028)."""
    builder, _mailbox, journal = _make_thread_builder(tmp_path, "[(thread-recap)]")
    tlon = TlonPaths(home=tmp_path)
    await _write_completed_session(
        journal,
        tlon,
        trigger="你有 2 封未读邮件",
        conclusion="配置里端口写成了 8081",
        tool_text="读取 config.toml 成功",
    )

    context = await _build(builder)

    assert len(context) == 2  # recap + wake trigger
    recap = context[0]
    assert recap["role"] == "user"
    assert recap["content"].startswith("[最近会话摘要]")
    assert "触发语：你有 2 封未读邮件" in recap["content"]
    assert "处理结果：complete" in recap["content"]
    assert "readFile: 读取 config.toml 成功" in recap["content"]
    assert "结论：配置里端口写成了 8081" in recap["content"]
    assert context[1]["content"] == TRIGGER


async def test_recap_bounded_to_latest_completed_session(tmp_path: Path) -> None:
    """Only the single most recent completed session (by log mtime) is
    recapped — the digest never spans multiple sessions."""
    builder, _mailbox, journal = _make_thread_builder(tmp_path, "[(thread-recap)]")
    tlon = TlonPaths(home=tmp_path)
    await _write_completed_session(
        journal, tlon, trigger="旧触发", conclusion="第一轮的结论", mtime=1000.0
    )
    await _write_completed_session(
        journal, tlon, trigger="新触发", conclusion="第二轮的结论", mtime=2000.0
    )

    context = await _build(builder)

    assert len(context) == 2
    recap = context[0]["content"]
    assert "第二轮的结论" in recap
    assert "第一轮的结论" not in recap
    assert recap.count("[最近会话摘要]") == 1


async def test_unfinished_latest_session_falls_back(tmp_path: Path) -> None:
    """The latest session without session/end (in flight or crashed) is
    skipped; the last *completed* one is the one recapped."""
    builder, _mailbox, journal = _make_thread_builder(tmp_path, "[(thread-recap)]")
    tlon = TlonPaths(home=tmp_path)
    await _write_completed_session(
        journal, tlon, trigger="完成轮", conclusion="完成轮的结论", mtime=1000.0
    )
    # A newer but unfinished session (no session/end).
    await journal.append(
        f"{AGENT}-s-unfinished",
        "session/start",
        agent_id=AGENT,
        trigger_content="没跑完的那轮",
        carrier=None,
        responsibility=[],
        context=[],
    )
    os.utime(tlon.agent_sessions_dir(AGENT) / f"{AGENT}-s-unfinished.jsonl", (2000.0, 2000.0))

    context = await _build(builder)

    assert len(context) == 2
    recap = context[0]["content"]
    assert "完成轮的结论" in recap
    assert "没跑完的那轮" not in recap


async def test_undeclared_thread_recap_injects_nothing(tmp_path: Path) -> None:
    """A spec without (thread-recap) never sees the recap, journal or not."""
    builder, _mailbox, journal = _make_thread_builder(tmp_path, "[(current-task)]")
    await _write_completed_session(
        journal, TlonPaths(home=tmp_path), trigger="旧触发", conclusion="旧结论"
    )

    context = await _build(builder)

    assert len(context) == 1
    assert context[0]["content"] == TRIGGER


async def test_recap_precedes_mail_history(tmp_path: Path) -> None:
    """Declared before mail-history, the recap lands ahead of the raw
    correspondence in vector order."""
    builder, mailbox, journal = _make_thread_builder(tmp_path, "[(thread-recap) (mail-history 5)]")
    mail1 = _mail("旧信")
    await mailbox.append_inbox(mail1)
    await _write_completed_session(
        journal, TlonPaths(home=tmp_path), trigger="旧触发", conclusion="旧结论"
    )

    context = await _build(builder)

    assert context[0]["content"].startswith("[最近会话摘要]")
    # Then the raw history (mail1) and the wake trigger.
    assert context[1]["content"].endswith("\n\n旧信")
    assert context[2]["content"] == TRIGGER


# ===== (mail-history F R) 两段式（ADR 0025）=====


async def test_two_tier_frozen_plus_recent(tmp_path: Path) -> None:
    """The frozen segment is the earliest F mails of the merged timeline,
    the sliding segment the most recent R — chronological order overall,
    with the same role mapping (own mail -> assistant)."""
    builder, mailbox, _journal = _make_thread_builder(tmp_path, "[(mail-history 2 2)]")
    await mailbox.append_inbox(_timed_mail("第1封", "2026-10-01T00:00:00+00:00"))
    own = Mail.create(from_address=ADDRESS, content="第2封", to=["alice@tlon.internal"])
    own.timestamp = "2026-10-02T00:00:00+00:00"
    await mailbox.append_sent(own)
    for i in range(3, 7):
        await mailbox.append_inbox(_timed_mail(f"第{i}封", f"2026-10-0{i}T00:00:00+00:00"))

    context = await _build(builder)

    history = context[:-1]
    assert [m["role"] for m in history] == ["user", "assistant", "user", "user"]
    assert "第1封" in history[0]["content"]
    assert "第2封" in history[1]["content"]
    assert "第5封" in history[2]["content"]
    assert "第6封" in history[3]["content"]
    assert all("第3封" not in m["content"] and "第4封" not in m["content"] for m in history)
    assert context[-1]["content"] == TRIGGER


async def test_two_tier_dedups_overlap(tmp_path: Path) -> None:
    """Total below F + R: the overlapping mails render exactly once."""
    builder, mailbox, _journal = _make_thread_builder(tmp_path, "[(mail-history 2 2)]")
    for i in range(1, 4):
        await mailbox.append_inbox(_timed_mail(f"第{i}封", f"2026-10-0{i}T00:00:00+00:00"))

    context = await _build(builder)

    history = [m["content"] for m in context[:-1]]
    assert len(history) == 3
    assert "第1封" in history[0]
    assert "第2封" in history[1]
    assert "第3封" in history[2]


async def test_two_tier_frozen_segment_byte_stable_across_new_mail(tmp_path: Path) -> None:
    """Once filled, the frozen segment never moves: a new mail only slides
    the recent window, so the previous session's context head stays a
    byte-identical prefix of the next one (provider prompt caching bills
    only the moved suffix)."""
    builder, mailbox, _journal = _make_thread_builder(tmp_path, "[(mail-history 2 1)]")
    for i in range(1, 4):
        await mailbox.append_inbox(_timed_mail(f"第{i}封", f"2026-10-0{i}T00:00:00+00:00"))
    context1 = await _build(builder)

    await mailbox.append_inbox(_timed_mail("第四封", "2026-10-05T00:00:00+00:00"))
    context2 = await _build(builder)

    # Frozen segment (第1封/第2封) byte-identical; only the sliding tail
    # and the trailing trigger text moved.
    assert json.dumps(context2[:2], ensure_ascii=False) == json.dumps(
        context1[:2], ensure_ascii=False
    )
    assert "第1封" in context2[0]["content"]
    assert "第2封" in context2[1]["content"]
    assert "第四封" in context2[2]["content"]
    assert "第三封" not in context2[2]["content"]
    assert context2[-1]["content"] == TRIGGER


def _timed_mail(content: str, timestamp: str) -> Mail:
    """A mail from the human with a fixed timestamp (ordering is explicit)."""
    mail = Mail.create(from_address="alice@tlon.internal", content=content, to=[ADDRESS])
    mail.timestamp = timestamp
    return mail


# ===== (lessons) section（docs/design/prototypes.md）=====


def _make_lessons_builder(tmp_path: Path, sections: str) -> ContextBuilder:
    """A builder whose lessons file resolves under the isolated home."""
    return ContextBuilder(
        AgentConfig(id=AGENT, name=AGENT),
        None,
        _MemoryStub(),  # type: ignore[arg-type]
        TaskManager(agent_id=AGENT, tlon_paths=TlonPaths(home=tmp_path)),
        _rule_with_sections(sections),
        ADDRESS,
        None,
        tlon_paths=TlonPaths(home=tmp_path),
    )


def test_lessons_section_accepted() -> None:
    spec = MemberSpec.from_edn(
        loads(
            """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions []
          :loop {:context {:sections [(persona) (lessons) (mail-history 5)]}
                 :stages [(think)]
                 :until (iteration-limit-reached?)
                 :max-iterations 1
                 :max-consecutive-errors 1
                 :max-tokens 1000000
                 :reflect {:enabled false :interval-minutes 15}}}]}
"""
        )
    )
    validate_member_spec(spec)


def test_lessons_section_rejects_arguments() -> None:
    spec = MemberSpec.from_edn(
        loads(
            """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions []
          :loop {:context {:sections [(lessons 3)]}
                 :stages [(think)]
                 :until (iteration-limit-reached?)
                 :max-iterations 1
                 :max-consecutive-errors 1
                 :max-tokens 1000000
                 :reflect {:enabled false :interval-minutes 15}}}]}
"""
        )
    )
    with pytest.raises(MemberSpecValidationError, match="takes no arguments"):
        validate_member_spec(spec)


async def test_lessons_file_injected(tmp_path: Path) -> None:
    builder = _make_lessons_builder(tmp_path, "[(lessons)]")
    lessons_file = TlonPaths(home=tmp_path).agent_lessons_file(AGENT)
    lessons_file.parent.mkdir(parents=True, exist_ok=True)
    lessons_file.write_text("- 部署前先跑质量门\n", encoding="utf-8")

    context = await _build(builder)

    assert len(context) == 2  # lessons block + wake trigger
    section = context[0]
    assert section["role"] == "user"
    assert section["content"].startswith("[传承教训]")
    assert "- 部署前先跑质量门" in section["content"]
    assert context[1]["content"] == TRIGGER


async def test_no_lessons_file_injects_nothing(tmp_path: Path) -> None:
    """A member without config/LESSONS.md simply skips the section."""
    builder = _make_lessons_builder(tmp_path, "[(lessons)]")

    context = await _build(builder)

    assert len(context) == 1
    assert context[0]["content"] == TRIGGER


async def test_undeclared_lessons_section_injects_nothing(tmp_path: Path) -> None:
    """A spec without (lessons) never sees the file — 看与不看看 spec."""
    builder = _make_lessons_builder(tmp_path, "[(persona)]")
    lessons_file = TlonPaths(home=tmp_path).agent_lessons_file(AGENT)
    lessons_file.parent.mkdir(parents=True, exist_ok=True)
    lessons_file.write_text("- 教训\n", encoding="utf-8")

    context = await _build(builder)

    assert len(context) == 1


# ===== (knowledge) 段：JIT 检索 query 传递（ADR 0017 阶段 2 / ADR 0028 D2）=====


class _RecordingMemory:
    """Duck-typed MemoryManager: records the retrieval query, returns one
    fixed claims block."""

    def __init__(self) -> None:
        self.queries: list[Any] = []

    async def build_context_blocks(self, current_message: Any | None = None) -> list[Any]:
        from tlon.memory.models import ContextBlock

        self.queries.append(current_message)
        return [
            ContextBlock(
                block_type="claims",
                priority=1,
                content="- [2026-09-17] 部署脚本在 scripts/deploy.sh",
                source_ids=["c1"],
            )
        ]


def _make_knowledge_builder(tmp_path: Path) -> tuple[ContextBuilder, _RecordingMemory]:
    memory = _RecordingMemory()
    builder = ContextBuilder(
        AgentConfig(id=AGENT, name=AGENT),
        None,
        memory,  # type: ignore[arg-type]
        TaskManager(agent_id=AGENT, tlon_paths=TlonPaths(home=tmp_path)),
        _rule_with_sections("[(knowledge)]"),
        ADDRESS,
        tlon_paths=TlonPaths(home=tmp_path),
    )
    return builder, memory


async def test_knowledge_query_is_responsibility_subjects_and_bodies(tmp_path: Path) -> None:
    """D2: the JIT retrieval query is the responsibility set's
    subject+content concatenation, one mail after another."""
    builder, memory = _make_knowledge_builder(tmp_path)
    mails = [
        _mail("怎么部署？", subject="部署问题"),
        _mail("另外看下监控", subject="监控告警"),
    ]

    context = (await builder.build_context(TRIGGER, mails)).messages

    assert memory.queries == ["部署问题\n\n怎么部署？\n\n监控告警\n\n另外看下监控"]
    section = context[-1]
    assert section["role"] == "user"
    assert section["content"].startswith("[已知信息]")
    assert "部署脚本在 scripts/deploy.sh" in section["content"]


async def test_knowledge_query_without_subject_is_body(tmp_path: Path) -> None:
    builder, memory = _make_knowledge_builder(tmp_path)

    await _build(builder)

    assert memory.queries == ["hello"]


# ===== (knowledge) 段：anchor 减肥——固定末位、位置语义失效（ADR 0017 阶段 4）=====


async def test_knowledge_lands_last_after_trigger(tmp_path: Path) -> None:
    """The knowledge message is the very last message — after the wake
    trigger text — wherever the section is declared in the vector."""
    memory = _RecordingMemory()
    tasks = TaskManager(agent_id=AGENT, tlon_paths=TlonPaths(home=tmp_path))
    task = tasks.create_task("进行中的任务")
    tasks.start_task(task.id)
    builder = ContextBuilder(
        AgentConfig(id=AGENT, name=AGENT),
        None,
        memory,  # type: ignore[arg-type]
        tasks,
        # knowledge declared mid-vector: its position no longer matters.
        _rule_with_sections("[(knowledge) (current-task)]"),
        ADDRESS,
        tlon_paths=TlonPaths(home=tmp_path),
    )

    built = await builder.build_context(TRIGGER, [_mail()])

    # task section, then the wake trigger, then knowledge — last of all.
    assert len(built.messages) == 3
    assert built.messages[0]["content"].startswith("[当前任务]")
    assert built.messages[1]["content"] == TRIGGER
    assert built.messages[2]["content"].startswith("[已知信息]")
    assert built.anchor_len == 2  # task + trigger; knowledge excluded


async def test_anchor_len_covers_everything_without_knowledge(tmp_path: Path) -> None:
    """A member without (knowledge) (fast-responder style): the anchor is
    the whole initial context, as before."""
    builder, tasks = _make_builder(tmp_path, "[(current-task)]")
    task = tasks.create_task("写周报")
    tasks.start_task(task.id)

    built = await builder.build_context(TRIGGER, [_mail()])

    assert built.anchor_len == len(built.messages) == 2


async def test_empty_knowledge_leaves_anchor_unchanged(tmp_path: Path) -> None:
    """Knowledge declared but nothing retrieved: no tail message, the
    anchor covers the whole context."""

    class _EmptyMemory:
        async def build_context_blocks(self, current_message: Any | None = None) -> list[Any]:
            return []

    builder = ContextBuilder(
        AgentConfig(id=AGENT, name=AGENT),
        None,
        _EmptyMemory(),  # type: ignore[arg-type]
        TaskManager(agent_id=AGENT, tlon_paths=TlonPaths(home=tmp_path)),
        _rule_with_sections("[(knowledge)]"),
        ADDRESS,
        tlon_paths=TlonPaths(home=tmp_path),
    )

    built = await builder.build_context(TRIGGER, [_mail()])

    assert len(built.messages) == 1  # only the wake trigger
    assert built.anchor_len == 1


# ===== 外部来信标记（docs/design/mail.md）=====

EXTERNAL = "notifications@github.com"
MARKER = "X-External: true"


async def test_mail_history_marks_only_external_mails(tmp_path: Path) -> None:
    builder, mailbox, _journal = _make_thread_builder(tmp_path, "[(mail-history 5)]")
    internal = _mail("内部来信")
    external = Mail.create(from_address=EXTERNAL, content="外部来信", to=[ADDRESS])
    await mailbox.append_inbox(internal)
    await mailbox.append_inbox(external)

    context = await _build(builder)

    history = [m["content"] for m in context[:-1]]
    assert len(history) == 2
    internal_msg = next(c for c in history if "内部来信" in c)
    external_msg = next(c for c in history if "外部来信" in c)
    assert MARKER not in internal_msg
    assert MARKER in external_msg
    assert f"From: {EXTERNAL}" in external_msg


def test_render_thread_recap_renders_projection() -> None:
    """Direct render check: trigger text, end reason, one clipped line per
    tool result, the last LLM response as the conclusion."""
    events = [
        {"type": "session/start", "trigger_content": "你有 3 封未读邮件"},
        {
            "type": "tool/result",
            "tool_call": {"function": {"name": "executeBashCommand"}},
            "result": {"message": "ran ok"},
        },
        {"type": "llm/response", "response": {"content": "先看一眼"}},
        {"type": "llm/response", "response": {"content": "磁盘正常"}},
        {"type": "session/end", "reason": "complete"},
    ]

    recap = _render_thread_recap(events)

    assert recap.startswith("[最近会话摘要]")
    assert "触发语：你有 3 封未读邮件" in recap
    assert "处理结果：complete" in recap
    assert "- executeBashCommand: ran ok" in recap
    assert "结论：磁盘正常" in recap
    assert "先看一眼" not in recap


def test_render_thread_recap_empty_events_returns_empty() -> None:
    assert _render_thread_recap([]) == ""
