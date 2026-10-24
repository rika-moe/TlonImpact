"""readRun tool (ADR 0017 stage 4): the claim → source read-back.

searchMemories exposes each claim's ``source_run_ids``; the tool
renders that run's journal as a bounded digest (trigger + replies +
tool activity), clips every piece, caps the total, and answers a missing
run with a clean error instead of an exception.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from tests.history_helpers import append_history
from tlon.agents.run_journal import RunJournal
from tlon.base.paths import TlonPaths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.memory.run_digest import (
    _RESPONSE_MAX,
    _RESPONSES_LIMIT,
    _TOTAL_MAX,
    render_run_digest,
)
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.memory import make_memory_operations
from tlon.operations.schema import OperationError


def _tool(tmp_path: Path) -> tuple[OperationTool, RunJournal]:
    journal = RunJournal("agent1", TlonPaths(home=tmp_path))
    memory = MemoryManager("agent1", MemoryConfig(), tmp_path / "agent1")
    executor = OperationExecutor(
        OperationRegistry(make_memory_operations(memory, journal)),
        CallContext("agent1", "run", "call"),
    )
    return OperationTool(executor, "run-read"), journal


async def _write_run(
    journal: RunJournal,
    sid: str,
    *,
    replies: list[str],
    tools: list[str] | None = None,
    end: bool = True,
) -> None:
    await append_history(
        journal,
        sid,
        "run/start",
        agent_id="agent1",
        mail_id="m1",
        trigger_content="部署脚本放在哪？",
        carrier=None,
        context=[{"role": "user", "content": "hi"}],
        anchor_len=1,
    )
    for index, name in enumerate(tools or []):
        await append_history(
            journal,
            sid,
            "tool/result",
            tool_call={
                "id": f"tc{index}",
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            },
            result={"success": True, "data": {}, "message": "ok", "error": None},
        )
    for reply in replies:
        await append_history(journal, sid, "llm/response", response={"content": reply})
    if end:
        await append_history(journal, sid, "run/end", reason="complete")


async def test_digest_covers_trigger_replies_tools_and_end(tmp_path: Path) -> None:
    tool, journal = _tool(tmp_path)
    sid = "agent1-s1"
    await _write_run(
        journal,
        sid,
        replies=["先查一下", "在 scripts/deploy.sh"],
        tools=["file-search", "file-read", "file-read"],
    )

    result = await tool.execute_context({"run_id": sid})

    assert result.success is True
    digest = result.data["digest"]
    assert "触发语：部署脚本放在哪？" in digest
    assert "结束：completed" in digest
    assert "工具调用：file-search ×1、file-read ×2" in digest
    assert "第 1 轮回复：先查一下" in digest
    assert "第 2 轮回复：在 scripts/deploy.sh" in digest
    assert result.data["run_id"] == sid


async def test_missing_run_is_a_clean_error(tmp_path: Path) -> None:
    tool, _ = _tool(tmp_path)

    result = await tool.execute_context({"run_id": "agent1-nope"})

    assert result.success is False
    assert "agent1-nope" in (result.error or "")
    assert result.data["outcome"] == "rejected"


async def test_long_reply_is_clipped(tmp_path: Path) -> None:
    tool, journal = _tool(tmp_path)
    sid = "agent1-long"
    await _write_run(journal, sid, replies=["长" * (_RESPONSE_MAX * 2)])

    result = await tool.execute_context({"run_id": sid})

    assert result.success is True
    digest = result.data["digest"]
    assert "长" * _RESPONSE_MAX in digest
    assert "长" * (_RESPONSE_MAX + 1) not in digest
    assert digest.endswith("…") or "…" in digest


async def test_reply_count_is_capped(tmp_path: Path) -> None:
    tool, journal = _tool(tmp_path)
    sid = "agent1-many"
    replies = [f"第{i}条回复" for i in range(_RESPONSES_LIMIT + 3)]
    await _write_run(journal, sid, replies=replies)

    result = await tool.execute_context({"run_id": sid})

    assert result.success is True
    digest = result.data["digest"]
    assert f"第 {_RESPONSES_LIMIT} 轮回复" in digest
    assert f"第 {_RESPONSES_LIMIT + 1} 轮回复" not in digest
    assert f"共 {len(replies)} 轮回复，仅显示前 {_RESPONSES_LIMIT} 轮" in digest


async def test_total_output_is_hard_capped(tmp_path: Path) -> None:
    """The backstop: whatever the events contain, the rendering never
    exceeds the total cap (here: a tool-activity line with thousands of
    distinct tool names)."""
    events = [
        {"type": "run/start", "trigger_content": "t"},
        *[
            {
                "type": "tool/result",
                "tool_call": {"function": {"name": f"tool{i}"}},
                "result": {},
            }
            for i in range(2000)
        ],
    ]

    digest = render_run_digest(events)

    assert len(digest) <= _TOTAL_MAX + 1  # +1: the … marker


class _BrokenLogs:
    async def read_events(self, run_id: str) -> list[dict[str, Any]]:
        raise OSError("disk gone")

    async def list_completed(self, started_after: datetime | None = None) -> list[str]:
        return []

    async def count_completed(self, started_after: datetime | None = None) -> int:
        return 0


async def test_journal_failure_propagates(tmp_path: Path) -> None:
    memory = MemoryManager("agent1", MemoryConfig(), tmp_path / "agent1")
    executor = OperationExecutor(
        OperationRegistry(make_memory_operations(memory, _BrokenLogs())),
        CallContext("agent1", "run", "call"),
    )
    tool = OperationTool(executor, "run-read")

    with pytest.raises(OperationError, match="disk gone"):
        await tool.execute_context({"run_id": "agent1-x"})
