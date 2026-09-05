"""ContextBuilder — the ``(current-task)`` section (issue ).

Pins the section semantics: the doing task (at most one) is rendered as
a ``[当前任务]`` user message in vector order; no doing task (including
the paused-only case — unlike ``TaskManager.get_current_task`` there is
no paused fallback here) skips the section entirely, matching the
knowledge/skills empty-state convention; a spec that does not declare
the section injects nothing. Also pins the DSL side: ``(current-task)``
is a valid no-argument section.
"""

from pathlib import Path
from typing import Any

import pytest

from tlon.agents.context_builder import ContextBuilder
from tlon.agents.edn import loads
from tlon.agents.member_spec import (
    MemberRule,
    MemberSpec,
    MemberSpecValidationError,
    validate_member_spec,
)
from tlon.base.config import AgentConfig
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.tools.tasks.manager import TaskManager

AGENT = "agent1"
ADDRESS = f"{AGENT}@tlon.internal"


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
                 :reflect {:enabled false :interval-minutes 15}}}]}
""".replace(
                "SECTIONS", sections
            )
        )
    )
    rule = spec.rules[0]
    assert rule.loop is not None
    return rule


def _make_builder(tmp_path: Path, sections: str) -> tuple[ContextBuilder, TaskManager]:
    task_manager = TaskManager(agent_id=AGENT, paths=TlonPaths(home=tmp_path))
    builder = ContextBuilder(
        AgentConfig(id=AGENT, name=AGENT),
        [],
        None,
        _MemoryStub(),  # type: ignore[arg-type]
        task_manager,
        _rule_with_sections(sections),
        ADDRESS,
    )
    return builder, task_manager


def _mail() -> Mail:
    return Mail.create(from_address="alice@tlon.internal", content="hello", to=[ADDRESS])


def _section_messages(context: list[dict[str, Any]]) -> list[str]:
    """All message contents except the trailing current-mail message."""
    return [m["content"] for m in context]


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

    context = await builder.build_context(_mail())

    assert len(context) == 2  # section message + current mail
    section = context[0]
    assert section["role"] == "user"
    assert section["content"].startswith("[当前任务]")
    assert "t-001: 写周报（doing）" in section["content"]
    assert "描述：汇总本周进展" in section["content"]
    assert context[1]["content"].endswith("\n\nhello")


async def test_no_task_injects_nothing(tmp_path: Path) -> None:
    builder, _ = _make_builder(tmp_path, "[(current-task)]")

    context = await builder.build_context(_mail())

    assert len(context) == 1  # only the current mail
    assert context[0]["content"].endswith("\n\nhello")


async def test_todo_task_not_injected(tmp_path: Path) -> None:
    builder, tasks = _make_builder(tmp_path, "[(current-task)]")
    tasks.create_task("还没开始的任务")

    context = await builder.build_context(_mail())

    assert len(context) == 1


async def test_paused_task_not_injected(tmp_path: Path) -> None:
    """A paused task is not the current task — no get_current_task fallback."""
    builder, tasks = _make_builder(tmp_path, "[(current-task)]")
    task = tasks.create_task("暂停的任务")
    tasks.start_task(task.id)
    tasks.pause_task(task.id)

    context = await builder.build_context(_mail())

    assert len(context) == 1


async def test_undeclared_section_injects_nothing(tmp_path: Path) -> None:
    """A spec without (current-task) never sees the task, doing or not."""
    builder, tasks = _make_builder(tmp_path, "[(persona)]")
    task = tasks.create_task("进行中的任务")
    tasks.start_task(task.id)

    context = await builder.build_context(_mail())

    assert len(context) == 1
    assert context[0]["content"].endswith("\n\nhello")


async def test_section_follows_vector_order(tmp_path: Path) -> None:
    """The section lands where the vector puts it — before mail-history here."""
    builder, tasks = _make_builder(tmp_path, "[(current-task) (mail-history 5)]")
    task = tasks.create_task("排序任务")
    tasks.start_task(task.id)

    context = await builder.build_context(_mail())

    # No mailbox history (mailbox=None degrades to empty): task, then mail.
    assert context[0]["content"].startswith("[当前任务]")
    assert context[1]["content"].endswith("\n\nhello")
