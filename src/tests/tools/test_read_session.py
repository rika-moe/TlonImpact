"""readSession tool (ADR 0017 stage 4): the claim → source read-back.

searchMemories exposes each claim's ``source_session_ids``; the tool
renders that session's journal as a bounded digest (trigger + replies +
tool activity), clips every piece, caps the total, and answers a missing
session with a clean error instead of an exception.
"""

from pathlib import Path
from typing import Any

from tlon.agents.session_journal import SessionJournal
from tlon.base.paths import TlonPaths
from tlon.tools.memory.read_session import (
    _RESPONSE_MAX,
    _RESPONSES_LIMIT,
    _TOTAL_MAX,
    ReadSessionTool,
    render_session_digest,
)


def _tool(tmp_path: Path) -> tuple[ReadSessionTool, SessionJournal]:
    journal = SessionJournal("agent1", TlonPaths(home=tmp_path))
    return ReadSessionTool(journal), journal


async def _write_session(
    journal: SessionJournal,
    sid: str,
    *,
    replies: list[str],
    tools: list[str] | None = None,
    end: bool = True,
) -> None:
    await journal.append(
        sid,
        "session/start",
        agent_id="agent1",
        mail_id="m1",
        trigger_content="部署脚本放在哪？",
        carrier=None,
        context=[{"role": "user", "content": "hi"}],
        anchor_len=1,
    )
    for index, name in enumerate(tools or []):
        await journal.append(
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
        await journal.append(sid, "llm/response", response={"content": reply})
    if end:
        await journal.append(sid, "session/end", reason="complete")


async def test_digest_covers_trigger_replies_tools_and_end(tmp_path: Path) -> None:
    tool, journal = _tool(tmp_path)
    sid = "agent1-s1"
    await _write_session(
        journal,
        sid,
        replies=["先查一下", "在 scripts/deploy.sh"],
        tools=["searchFiles", "readFile", "readFile"],
    )

    result = await tool.execute(session_id=sid)

    assert result.success is True
    digest = result.data["digest"]
    assert "触发：部署脚本放在哪？" in digest
    assert "结束：complete" in digest
    assert "工具调用：searchFiles ×1、readFile ×2" in digest
    assert "第 1 轮回复：先查一下" in digest
    assert "第 2 轮回复：在 scripts/deploy.sh" in digest
    assert result.data["session_id"] == sid


async def test_missing_session_is_a_clean_error(tmp_path: Path) -> None:
    tool, _ = _tool(tmp_path)

    result = await tool.execute(session_id="agent1-nope")

    assert result.success is False
    assert "agent1-nope" in (result.error or "")
    assert "不存在" in (result.message or "")


async def test_long_reply_is_clipped(tmp_path: Path) -> None:
    tool, journal = _tool(tmp_path)
    sid = "agent1-long"
    await _write_session(journal, sid, replies=["长" * (_RESPONSE_MAX * 2)])

    result = await tool.execute(session_id=sid)

    assert result.success is True
    digest = result.data["digest"]
    assert "长" * _RESPONSE_MAX in digest
    assert "长" * (_RESPONSE_MAX + 1) not in digest
    assert digest.endswith("…") or "…" in digest


async def test_reply_count_is_capped(tmp_path: Path) -> None:
    tool, journal = _tool(tmp_path)
    sid = "agent1-many"
    replies = [f"第{i}条回复" for i in range(_RESPONSES_LIMIT + 3)]
    await _write_session(journal, sid, replies=replies)

    result = await tool.execute(session_id=sid)

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
        {"type": "session/start", "trigger_content": "t"},
        *[
            {
                "type": "tool/result",
                "tool_call": {"function": {"name": f"tool{i}"}},
                "result": {},
            }
            for i in range(2000)
        ],
    ]

    digest = render_session_digest(events)

    assert len(digest) <= _TOTAL_MAX + 1  # +1: the … marker


class _BrokenLogs:
    async def read_events(self, session_id: str) -> list[dict[str, Any]]:
        raise OSError("disk gone")

    async def list_completed(self) -> list[str]:
        return []


async def test_journal_failure_is_a_clean_error(tmp_path: Path) -> None:
    tool = ReadSessionTool(_BrokenLogs())

    result = await tool.execute(session_id="agent1-x")

    assert result.success is False
    assert "disk gone" in (result.message or "")
