"""Journal -> claims batch distillation (ADR 0017).

Pins the distillation flow: the scheduler's reap point calls
``MemoryManager.maybe_distill_completed_sessions(journal)``; the pending
set is the completed sessions whose ``session/start`` timestamp is newer
than the persisted watermark (a scalar timestamp — session ids are
uuids without temporal order); the batch is distilled from projections
rebuilt out of each session's journal into claims; the watermark
advances only after the claims land.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from tlon.agents.session_journal import SessionJournal
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.base.paths import TlonPaths
from tlon.memory.distiller import Distiller
from tlon.memory.manager import MemoryManager
from tlon.memory.models import Claim, MemoryConfig, SessionProjection, build_session_projection
from tlon.tools.base import ToolResult


def _journal(tmp_path: Path) -> SessionJournal:
    return SessionJournal("agent1", TlonPaths(home=tmp_path))


def _manager(tmp_path: Path, distillation_window: int = 3) -> MemoryManager:
    # No llm_config: the fallback (mechanical) claim path is exercised.
    return MemoryManager(
        "agent1",
        MemoryConfig(distillation_window=distillation_window),
        data_dir=tmp_path / "agent1",
    )


def _watermark(tmp_path: Path) -> str | None:
    file = tmp_path / "agent1" / "memory" / "distillation.json"
    if not file.exists():
        return None
    return json.loads(file.read_text(encoding="utf-8")).get("last_distilled_at")


async def _completed_session(
    journal: SessionJournal, session_id: str, trigger: str, reply: str = "done"
) -> None:
    """Journal one minimal completed session: start, one LLM round, one
    tool result, end."""
    tc = ToolCall(id=f"{session_id}-c1", function_name="shell-run", arguments={})
    await journal.append(
        session_id,
        "session/start",
        agent_id="agent1",
        mail_id=f"m-{session_id}",
        trigger_content=trigger,
        carrier=None,
        context=[{"role": "user", "content": trigger}],
    )
    await journal.append(session_id, "llm/response", response=LLMResponse(content=reply).to_dict())
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
        # An unfinished session (no session/end) is not completed.
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

    async def test_empty_database_yields_nothing(self, tmp_path: Path) -> None:
        assert await _journal(tmp_path).list_completed() == []


# ── Projection building ─────────────────────────────────────────────────


class TestProjection:
    async def test_projection_carries_trigger_rounds_and_tools(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        await _completed_session(journal, "agent1-p", "帮我查一下磁盘", reply="已经查完")

        events = await journal.read_events("agent1-p")
        projection = build_session_projection(events, "agent1-p")

        assert projection.session_id == "agent1-p"
        # The real trigger — not the agent's own reply (the retired
        # working memory quoted the reply and impersonated user input).
        assert projection.trigger_content == "帮我查一下磁盘"
        assert projection.llm_responses == ["已经查完"]
        assert projection.tool_results == ["shell-run: ran ok"]
        assert projection.end_reason == "complete"


# ── Batch trigger threshold ─────────────────────────────────────────────


class TestBatchTrigger:
    async def test_below_window_does_not_distill(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, distillation_window=3)
        for i in range(2):
            await _completed_session(journal, f"agent1-s{i}", f"请求{i}")

        assert await manager.maybe_distill_completed_sessions(journal) is None
        assert await manager.claims.get_all_claims() == []
        assert _watermark(tmp_path) is None

    async def test_at_window_distills_whole_batch(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, distillation_window=3)
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"请求{i}")

        claim_count = await manager.maybe_distill_completed_sessions(journal)

        assert claim_count is not None and claim_count > 0
        claims = await manager.claims.get_all_claims()
        assert claims
        for claim in claims:
            assert claim["source_session_ids"] == ["agent1-s0", "agent1-s1", "agent1-s2"]
            assert claim["status"] == "active"
            assert claim["valid_from"]
        assert _watermark(tmp_path) is not None

    async def test_fallback_claims_quote_real_triggers(self, tmp_path: Path) -> None:
        """The fallback (no LLM) claims carry the sessions' real trigger
        content, never the agent's reply."""
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, distillation_window=3)
        await _completed_session(journal, "agent1-s0", "把部署文档整理好", reply="好的")
        await _completed_session(journal, "agent1-s1", "中间请求", reply="收到")
        await _completed_session(journal, "agent1-s2", "检查一下备份策略", reply="完成")

        await manager.maybe_distill_completed_sessions(journal)

        texts = [c["text"] for c in await manager.claims.get_all_claims()]
        assert any("把部署文档整理好" in t for t in texts)
        assert any("检查一下备份策略" in t for t in texts)
        # The agent's own replies must not be mistaken for user input.
        assert not any(t.startswith("好的") for t in texts)


# ── Watermark progress ──────────────────────────────────────────────────


class TestWatermark:
    async def test_distilled_sessions_are_not_redistilled(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, distillation_window=3)
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"请求{i}")
        assert await manager.maybe_distill_completed_sessions(journal) is not None
        first_watermark = _watermark(tmp_path)
        claim_count = len(await manager.claims.get_all_claims())

        # No new completed sessions: the second check is a no-op.
        assert await manager.maybe_distill_completed_sessions(journal) is None
        assert len(await manager.claims.get_all_claims()) == claim_count

        # Two more completions: pending = 2 < window, still no distillation.
        for i in range(3, 5):
            await _completed_session(journal, f"agent1-s{i}", f"请求{i}")
        assert await manager.maybe_distill_completed_sessions(journal) is None

        # The third new completion reaches the window: only the new
        # sessions are distilled, and the watermark advances.
        await _completed_session(journal, "agent1-s5", "请求5")
        assert await manager.maybe_distill_completed_sessions(journal) is not None
        new_claims = (await manager.claims.get_all_claims())[claim_count:]
        assert new_claims
        for claim in new_claims:
            assert claim["source_session_ids"] == ["agent1-s3", "agent1-s4", "agent1-s5"]
        assert _watermark(tmp_path) != first_watermark

    async def test_empty_reap_filters_watermark_before_reading_events(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, distillation_window=1)
        await _completed_session(journal, "agent1-s0", "已完成")
        assert await manager.maybe_distill_completed_sessions(journal) is not None
        original_read_events = journal.read_events
        read_count = 0

        async def counting_read_events(session_id: str) -> list[dict[str, Any]]:
            nonlocal read_count
            read_count += 1
            return await original_read_events(session_id)

        journal.read_events = counting_read_events  # type: ignore[method-assign]

        assert await manager.maybe_distill_completed_sessions(journal) is None
        assert read_count == 0

    async def test_watermark_survives_restart(self, tmp_path: Path) -> None:
        """A fresh manager over the same directory resumes from the
        persisted watermark instead of re-distilling history."""
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, distillation_window=3)
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"请求{i}")
        assert await manager.maybe_distill_completed_sessions(journal) is not None
        claim_count = len(await manager.claims.get_all_claims())

        restarted = _manager(tmp_path, distillation_window=3)
        assert await restarted.maybe_distill_completed_sessions(journal) is None
        assert len(await restarted.claims.get_all_claims()) == claim_count

    async def test_persist_failure_does_not_advance_watermark(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, distillation_window=3)
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"请求{i}")
        await manager.load()

        class _BoomStore:
            async def add_claim(self, claim: dict[str, Any]) -> str:
                raise OSError("disk full")

        manager.claims = _BoomStore()  # type: ignore[assignment]

        assert await manager.maybe_distill_completed_sessions(journal) is None
        assert _watermark(tmp_path) is None

    async def test_session_without_start_time_is_always_pending(self, tmp_path: Path) -> None:
        """A session whose log carries no usable start timestamp is never
        silently skipped — it stays pending (the safe direction)."""

        class _TimelessReader:
            async def list_completed(self, started_after: datetime | None = None) -> list[str]:
                return ["agent1-x"]

            async def count_completed(self, started_after: datetime | None = None) -> int:
                return 1

            async def read_events(self, session_id: str) -> list[dict[str, Any]]:
                return [{"type": "session/start", "trigger_content": "q"}]  # no ts

        manager = _manager(tmp_path, distillation_window=3)
        await manager._save_watermark("2999-01-01T00:00:00+00:00")

        pending = await manager._collect_pending(_TimelessReader())
        assert [sid for sid, _ts, _events in pending] == ["agent1-x"]


# ── LLM path: projections reach the distiller ───────────────────────────


class _FakeDistiller:
    """Duck-typed Distiller recording its input."""

    def __init__(self) -> None:
        self.seen: list[SessionProjection] | None = None

    async def distill(self, sessions: list[SessionProjection]) -> list[dict[str, Any]]:
        self.seen = list(sessions)
        return [{"text": "用户关注部署文档", "importance": 0.7}]


class TestLLMPath:
    async def test_projections_flow_into_distiller(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, distillation_window=3)
        fake = _FakeDistiller()
        manager.distiller = fake  # type: ignore[assignment]
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"真实触发{i}")

        claim_count = await manager.maybe_distill_completed_sessions(journal)

        assert claim_count == 1
        assert fake.seen is not None
        assert [p.session_id for p in fake.seen] == ["agent1-s0", "agent1-s1", "agent1-s2"]
        assert all(p.trigger_content == f"真实触发{i}" for i, p in enumerate(fake.seen))
        claim = Claim.from_dict((await manager.claims.get_all_claims())[0])
        assert claim.text == "用户关注部署文档"
        assert claim.importance == 0.7
        assert claim.source_session_ids == ["agent1-s0", "agent1-s1", "agent1-s2"]

    async def test_llm_failure_falls_back_to_mechanical_claims(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, distillation_window=3)

        class _Boom:
            async def distill(self, sessions: list[SessionProjection]) -> list[dict[str, Any]]:
                raise RuntimeError("llm down")

        manager.distiller = _Boom()  # type: ignore[assignment]
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"触发{i}")

        claim_count = await manager.maybe_distill_completed_sessions(journal)

        assert claim_count is not None and claim_count > 0
        texts = [c["text"] for c in await manager.claims.get_all_claims()]
        assert any("触发0" in t for t in texts)  # fallback quotes real triggers
        assert _watermark(tmp_path) is not None

    async def test_llm_empty_result_falls_back_to_mechanical_claims(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, distillation_window=3)

        class _Empty:
            async def distill(self, sessions: list[SessionProjection]) -> list[dict[str, Any]]:
                return []

        manager.distiller = _Empty()  # type: ignore[assignment]
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"触发{i}")

        claim_count = await manager.maybe_distill_completed_sessions(journal)

        assert claim_count is not None and claim_count > 0
        texts = [c["text"] for c in await manager.claims.get_all_claims()]
        assert any("触发0" in t for t in texts)  # fallback quotes real triggers
        assert _watermark(tmp_path) is not None

    def test_fallback_claims_use_trigger_content(self) -> None:
        sessions = [
            SessionProjection(session_id="s1", trigger_content="首个请求内容"),
            SessionProjection(
                session_id="s2", trigger_content="最后请求内容", llm_responses=["已处理"]
            ),
        ]
        claims = Distiller.fallback_claims(sessions)
        assert any("首个请求内容" in c["text"] for c in claims)
        assert any("最后请求内容" in c["text"] and "已处理" in c["text"] for c in claims)


# ── Digest ───────────────────────────────────────────────────────────────


class TestDigest:
    async def test_digest_counts_undistilled_sessions_without_reading_events(
        self, tmp_path: Path
    ) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, distillation_window=3)
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"请求{i}")
        original_read_events = journal.read_events
        read_count = 0

        async def counting_read_events(session_id: str) -> list[dict[str, Any]]:
            nonlocal read_count
            read_count += 1
            return await original_read_events(session_id)

        journal.read_events = counting_read_events  # type: ignore[method-assign]

        digest = await manager.get_memory_digest(journal)
        assert digest["undistilled_session_count"] == 3
        assert digest["claim_count"] == 0
        assert digest["claims"] == []
        assert read_count == 0

        assert await manager.maybe_distill_completed_sessions(journal) is not None
        read_count = 0

        digest = await manager.get_memory_digest(journal)
        assert digest["undistilled_session_count"] == 0
        assert digest["claim_count"] == len(digest["claims"]) > 0
        assert read_count == 0
