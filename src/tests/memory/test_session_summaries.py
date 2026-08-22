"""Journal -> episodic batch summarization (ADR 0009).

Pins the new summary flow: the scheduler's reap point calls
``MemoryManager.maybe_summarize_completed_sessions(journal)``; the
pending set is ``SessionJournal.list_completed()`` minus the
``source_session_ids`` recorded by recent summaries; the batch is
summarized from projections rebuilt out of each session's journal —
the real trigger content (never the agent's own reply), every round's
LLM response content, and one-line tool result digests.
"""

from pathlib import Path

from tlon.agents.session_journal import SessionJournal
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.base.paths import TlonPaths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig, SessionProjection, Summary
from tlon.memory.summarizer import Summarizer
from tlon.tools.base import ToolResult


def _journal(tmp_path: Path) -> SessionJournal:
    return SessionJournal("agent1", TlonPaths(home=tmp_path))


def _manager(tmp_path: Path, summary_window: int = 3) -> MemoryManager:
    # No llm_config: the fallback (mechanical) summary path is exercised.
    return MemoryManager(
        "agent1", MemoryConfig(summary_window=summary_window), data_dir=tmp_path / "agent1"
    )


async def _completed_session(
    journal: SessionJournal, session_id: str, trigger: str, reply: str = "done"
) -> None:
    """Journal one minimal completed session: start, one LLM round, one
    tool result, end."""
    tc = ToolCall(id=f"{session_id}-c1", function_name="executeBashCommand", arguments={})
    await journal.append(
        session_id,
        "session/start",
        agent_id="agent1",
        mail_id=f"m-{session_id}",
        trigger_content=trigger,
        carrier=None,
        context=[{"role": "user", "content": trigger}],
    )
    await journal.append(
        session_id, "llm/response", response=LLMResponse(content=reply).to_dict()
    )
    await journal.append(session_id, "tool/dispatch", tool_calls=[tc.to_dict()])
    await journal.append(
        session_id,
        "tool/result",
        tool_call=tc.to_dict(),
        result=ToolResult(success=True, data={}, message="ran ok").to_dict(),
    )
    await journal.append(session_id, "session/end", reason="complete")


# ── list_completed enumeration ─────────────────────────────────────────


class TestListCompleted:
    async def test_only_logs_with_session_end_are_completed(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        await _completed_session(journal, "agent1-a", "q1")
        # An unfinished log (no session/end) is not completed.
        await journal.append(
            "agent1-live",
            "session/start",
            agent_id="agent1",
            mail_id="m-live",
            trigger_content="q2",
            carrier=None,
            context=[],
        )

        assert await journal.list_completed() == ["agent1-a"]

    async def test_empty_sessions_dir_yields_nothing(self, tmp_path: Path) -> None:
        assert await _journal(tmp_path).list_completed() == []


# ── Projection building ─────────────────────────────────────────────────


class TestProjection:
    async def test_projection_carries_trigger_rounds_and_tools(
        self, tmp_path: Path
    ) -> None:
        journal = _journal(tmp_path)
        await _completed_session(journal, "agent1-p", "帮我查一下磁盘", reply="已经查完")

        events = await journal.read_events("agent1-p")
        projection = MemoryManager._build_projection("agent1-p", events)

        assert projection.session_id == "agent1-p"
        # The real trigger — not the agent's own reply (the retired
        # working memory quoted the reply and impersonated user input).
        assert projection.trigger_content == "帮我查一下磁盘"
        assert projection.llm_responses == ["已经查完"]
        assert projection.tool_results == ["executeBashCommand: ran ok"]


# ── Batch trigger threshold ─────────────────────────────────────────────


class TestBatchTrigger:
    async def test_below_window_does_not_summarize(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, summary_window=3)
        for i in range(2):
            await _completed_session(journal, f"agent1-s{i}", f"请求{i}")

        assert await manager.maybe_summarize_completed_sessions(journal) is None
        assert await manager.episodic.get_all_summaries() == []

    async def test_at_window_summarizes_whole_batch(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, summary_window=3)
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"请求{i}")

        summary_id = await manager.maybe_summarize_completed_sessions(journal)

        assert summary_id is not None
        summaries = await manager.episodic.get_all_summaries()
        assert len(summaries) == 1
        summary = Summary.from_dict(summaries[0])
        assert summary.source_session_ids == ["agent1-s0", "agent1-s1", "agent1-s2"]

    async def test_fallback_summary_quotes_real_triggers(self, tmp_path: Path) -> None:
        """The fallback (no LLM) summary carries the sessions' real trigger
        content, never the agent's reply."""
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, summary_window=3)
        await _completed_session(journal, "agent1-s0", "把部署文档整理好", reply="好的")
        await _completed_session(journal, "agent1-s1", "中间请求", reply="收到")
        await _completed_session(journal, "agent1-s2", "检查一下备份策略", reply="完成")

        await manager.maybe_summarize_completed_sessions(journal)

        summary = Summary.from_dict((await manager.episodic.get_all_summaries())[0])
        assert "把部署文档整理好" in summary.summary_text
        assert "检查一下备份策略" in summary.summary_text
        # The agent's own replies must not be mistaken for user input.
        assert "好的" not in summary.summary_text


# ── Dedup via source_session_ids ────────────────────────────────────────


class TestDedup:
    async def test_summarized_sessions_are_not_resummarized(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, summary_window=3)
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"请求{i}")
        assert await manager.maybe_summarize_completed_sessions(journal) is not None

        # No new completed sessions: the second check is a no-op.
        assert await manager.maybe_summarize_completed_sessions(journal) is None
        assert len(await manager.episodic.get_all_summaries()) == 1

        # Two more completions: pending = 2 < window, still no summary.
        for i in range(3, 5):
            await _completed_session(journal, f"agent1-s{i}", f"请求{i}")
        assert await manager.maybe_summarize_completed_sessions(journal) is None

        # The third new completion reaches the window: only the new
        # sessions are summarized.
        await _completed_session(journal, "agent1-s5", "请求5")
        assert await manager.maybe_summarize_completed_sessions(journal) is not None
        summaries = await manager.episodic.get_all_summaries()
        assert len(summaries) == 2
        second = Summary.from_dict(summaries[1])
        assert second.source_session_ids == ["agent1-s3", "agent1-s4", "agent1-s5"]


# ── LLM path: projections reach the summarizer ──────────────────────────


class _FakeSummarizer:
    """Duck-typed Summarizer recording its input."""

    def __init__(self) -> None:
        self.seen: list[SessionProjection] | None = None

    async def process_sessions(self, sessions, existing_facts=None):  # type: ignore[no-untyped-def]
        self.seen = list(sessions)
        return (
            {
                "timestamp": "2026-08-22T00:00:00",
                "summary_text": "批量摘要",
                "key_decisions": [],
                "action_items": [],
                "interaction_count": 3,
            },
            [],
        )


class TestLLMPath:
    async def test_projections_flow_into_summarizer(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, summary_window=3)
        fake = _FakeSummarizer()
        manager.summarizer = fake  # type: ignore[assignment]
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"真实触发{i}")

        summary_id = await manager.maybe_summarize_completed_sessions(journal)

        assert summary_id is not None
        assert fake.seen is not None
        assert [p.session_id for p in fake.seen] == ["agent1-s0", "agent1-s1", "agent1-s2"]
        assert all(p.trigger_content == f"真实触发{i}" for i, p in enumerate(fake.seen))
        summary = Summary.from_dict((await manager.episodic.get_all_summaries())[0])
        assert summary.summary_text == "批量摘要"
        assert summary.source_session_ids == ["agent1-s0", "agent1-s1", "agent1-s2"]

    async def test_llm_failure_falls_back_to_mechanical_summary(
        self, tmp_path: Path
    ) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, summary_window=3)

        class _Boom:
            async def process_sessions(self, sessions, existing_facts=None):  # type: ignore[no-untyped-def]
                raise RuntimeError("llm down")

        manager.summarizer = _Boom()  # type: ignore[assignment]
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"触发{i}")

        summary_id = await manager.maybe_summarize_completed_sessions(journal)

        assert summary_id is not None
        summary = Summary.from_dict((await manager.episodic.get_all_summaries())[0])
        assert "触发0" in summary.summary_text  # fallback quotes real triggers

    def test_fallback_summary_helper_uses_trigger_content(self) -> None:
        sessions = [
            SessionProjection(session_id="s1", trigger_content="首个请求内容"),
            SessionProjection(session_id="s2", trigger_content="最后请求内容"),
        ]
        text = Summarizer._generate_fallback_summary(sessions)
        assert "首个请求内容" in text
        assert "最后请求内容" in text
