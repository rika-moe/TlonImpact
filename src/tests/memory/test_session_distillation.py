"""Completed interaction sources -> claims with durable identity-based consumption.

A pending batch freezes projections, model receipts and stable claim writes;
progress never depends on source start-time ordering.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from tests.history_helpers import append_history
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


def _consumed_ids(tmp_path: Path) -> list[str] | None:
    file = tmp_path / "agent1" / "memory" / "distillation.json"
    if not file.exists():
        return None
    return json.loads(file.read_text(encoding="utf-8")).get("consumed_source_ids")


async def _completed_session(
    journal: SessionJournal, session_id: str, trigger: str, reply: str = "done"
) -> None:
    """Journal one minimal completed session: start, one LLM round, one
    tool result, end."""
    tc = ToolCall(id=f"{session_id}-c1", function_name="shell-run", arguments={})
    await append_history(
        journal,
        session_id,
        "session/start",
        agent_id="agent1",
        mail_id=f"m-{session_id}",
        trigger_content=trigger,
        carrier=None,
        context=[{"role": "user", "content": trigger}],
    )
    await append_history(
        journal, session_id, "llm/response", response=LLMResponse(content=reply).to_dict()
    )
    await append_history(journal, session_id, "tool/dispatch", tool_calls=[tc.to_dict()])
    await append_history(
        journal,
        session_id,
        "tool/result",
        tool_call=tc.to_dict(),
        result=ToolResult(success=True, data={}, message="ran ok").to_dict(),
    )
    await append_history(journal, session_id, "session/end", reason="complete")


# ── list_completed enumeration ─────────────────────────────────────────


class TestListCompleted:
    async def test_only_logs_with_session_end_are_completed(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        await _completed_session(journal, "agent1-a", "q1")
        # An unfinished session (no session/end) is not completed.
        await append_history(
            journal,
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
        assert _consumed_ids(tmp_path) is None

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
        assert _consumed_ids(tmp_path) is not None

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


class TestConsumptionReceipts:
    async def test_distilled_sessions_are_not_redistilled(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, distillation_window=3)
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"请求{i}")
        assert await manager.maybe_distill_completed_sessions(journal) is not None
        first_consumed_ids = _consumed_ids(tmp_path)
        claim_count = len(await manager.claims.get_all_claims())

        # No new completed sessions: the second check is a no-op.
        assert await manager.maybe_distill_completed_sessions(journal) is None
        assert len(await manager.claims.get_all_claims()) == claim_count

        # Two more completions: pending = 2 < window, still no distillation.
        for i in range(3, 5):
            await _completed_session(journal, f"agent1-s{i}", f"请求{i}")
        assert await manager.maybe_distill_completed_sessions(journal) is None

        # The third new completion reaches the window: only the new
        # sources are distilled, and their consumption receipts are saved.
        await _completed_session(journal, "agent1-s5", "请求5")
        assert await manager.maybe_distill_completed_sessions(journal) is not None
        new_claims = (await manager.claims.get_all_claims())[claim_count:]
        assert new_claims
        for claim in new_claims:
            assert claim["source_session_ids"] == ["agent1-s3", "agent1-s4", "agent1-s5"]
        assert _consumed_ids(tmp_path) != first_consumed_ids

    async def test_empty_reap_filters_consumed_ids_before_reading_events(
        self, tmp_path: Path
    ) -> None:
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

    async def test_consumed_ids_survives_restart(self, tmp_path: Path) -> None:
        """A fresh manager over the same directory resumes from the
        persisted source receipts instead of re-distilling history."""
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, distillation_window=3)
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"请求{i}")
        assert await manager.maybe_distill_completed_sessions(journal) is not None
        claim_count = len(await manager.claims.get_all_claims())

        restarted = _manager(tmp_path, distillation_window=3)
        assert await restarted.maybe_distill_completed_sessions(journal) is None
        assert len(await restarted.claims.get_all_claims()) == claim_count

    async def test_persist_failure_does_not_advance_consumed_ids(self, tmp_path: Path) -> None:
        journal = _journal(tmp_path)
        manager = _manager(tmp_path, distillation_window=3)
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"请求{i}")
        await manager.load()

        class _BoomStore:
            async def get_claim(self, claim_id: str) -> None:
                return None

            async def add_claim(self, claim: dict[str, Any]) -> str:
                raise OSError("disk full")

        manager.claims = _BoomStore()  # type: ignore[assignment]

        import pytest

        with pytest.raises(OSError):
            await manager.maybe_distill_completed_sessions(journal)
        assert not _consumed_ids(tmp_path)

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
        manager.distillation_file.write_text(
            json.dumps({"last_distilled_at": "2999-01-01T00:00:00+00:00"})
        )
        await manager.load()

        pending = await manager._collect_pending(_TimelessReader())
        assert [sid for sid, _events in pending] == ["agent1-x"]


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
                return []

        manager.distiller = _Boom()  # type: ignore[assignment]
        for i in range(3):
            await _completed_session(journal, f"agent1-s{i}", f"触发{i}")

        claim_count = await manager.maybe_distill_completed_sessions(journal)

        assert claim_count is not None and claim_count > 0
        texts = [c["text"] for c in await manager.claims.get_all_claims()]
        assert any("触发0" in t for t in texts)  # fallback quotes real triggers
        assert _consumed_ids(tmp_path) is not None

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
        assert _consumed_ids(tmp_path) is not None

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
        assert read_count == 3
        assert len(digest["recent_interactions"]) == 3

        assert await manager.maybe_distill_completed_sessions(journal) is not None
        read_count = 0

        digest = await manager.get_memory_digest(journal)
        assert digest["undistilled_session_count"] == 0
        assert digest["claim_count"] == len(digest["claims"]) > 0
        assert read_count == 3


async def test_late_earlier_completion_and_legacy_claim_receipts(tmp_path: Path) -> None:
    """Start-time ordering and old scalar metadata cannot hide late history."""
    journal = _journal(tmp_path)
    manager = _manager(tmp_path, distillation_window=1)
    await manager.load()
    old = Claim(
        text="old",
        timestamp="2020-01-01",
        valid_from="2020-01-01",
        source_session_ids=["legacy-consumed"],
    )
    historical_claim = old.to_dict()
    historical_claim.pop("origin")
    await manager.claims.add_claim(historical_claim)
    manager.distillation_file.write_text(
        json.dumps({"last_distilled_at": "2999-01-01T00:00:00+00:00"})
    )
    await _completed_session(journal, "legacy-consumed", "already consumed")
    await append_history(
        journal,
        "early",
        "session/start",
        ts="2000-01-01T00:00:00+00:00",
        trigger_content="late completion",
    )
    await _completed_session(journal, "newer", "newer completion")
    restarted = _manager(tmp_path, distillation_window=1)
    assert await restarted.maybe_distill_completed_sessions(journal) == 1
    await append_history(journal, "early", "session/end", reason="complete")
    assert await restarted.maybe_distill_completed_sessions(journal) == 1
    claims = await restarted.claims.get_all_claims()
    assert len(claims) == 3
    assert claims[-1]["source_session_ids"] == ["early"]
    assert await restarted.maybe_distill_completed_sessions(journal) is None


async def test_progress_commit_failure_replays_fixed_batch_without_model_or_claim_duplicates(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import pytest

    journal = _journal(tmp_path)
    await _completed_session(journal, "source", "important fact")
    manager = _manager(tmp_path, distillation_window=1)
    fake = _FakeDistiller()
    calls = 0
    original_distill = fake.distill

    async def distill(projections: list[SessionProjection]) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        return await original_distill(projections)

    fake.distill = distill  # type: ignore[method-assign]
    manager.distiller = fake  # type: ignore[assignment]
    save = manager._save_distillation

    async def fail_final_save() -> None:
        if manager._pending_batch is None:
            raise OSError("progress disk full")
        await save()

    monkeypatch.setattr(manager, "_save_distillation", fail_final_save)
    with pytest.raises(OSError):
        await manager.maybe_distill_completed_sessions(journal)
    assert len(await manager.claims.get_all_claims()) == 1
    # The same pending batch is resumed even when another source has arrived.
    await _completed_session(journal, "later", "later fact")
    restarted = _manager(tmp_path, distillation_window=1)
    restarted.distiller = fake  # type: ignore[assignment]
    assert await restarted.maybe_distill_completed_sessions(journal) == 1
    assert calls == 1
    assert len(await restarted.claims.get_all_claims()) == 1
    assert restarted._consumed == {"source"}
    assert await restarted.maybe_distill_completed_sessions(journal) == 1
    assert calls == 2


async def test_empty_source_is_consumed_without_endless_retries(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    await append_history(journal, "empty", "session/start", trigger_content="")
    await append_history(journal, "empty", "session/end", reason="complete")
    manager = _manager(tmp_path, distillation_window=1)
    assert await manager.maybe_distill_completed_sessions(journal) == 0
    assert await manager.maybe_distill_completed_sessions(journal) is None


async def test_remember_stable_receipt_does_not_repeat_conflict_judgment(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock

    manager = _manager(tmp_path)
    manager._resolve_conflicts = AsyncMock()  # type: ignore[method-assign]
    for _ in range(2):
        assert (
            await manager.remember_fact(
                "fact", claim_id="reflection:batch:0", source_session_ids=["program:run"]
            )
            == "reflection:batch:0"
        )
    assert manager._resolve_conflicts.await_count == 1
    claims = await manager.claims.get_all_claims()
    assert len(claims) == 1
    assert claims[0]["source_session_ids"] == ["program:run"]


async def test_distillation_receipt_replays_after_model_output_checkpoint_failure(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A provider response committed in ProgramStore survives the JSON checkpoint gap."""
    import pytest

    from tlon.agents.program_store import ProgramStore
    from tlon.base.config import LLMConfig
    from tlon.base.execution import ExecutionScope, bind_execution
    from tlon.base.llm_client import LLMClient

    provider_calls = 0

    async def provider(self: LLMClient, *args: Any, **kwargs: Any) -> LLMResponse:
        nonlocal provider_calls
        provider_calls += 1
        return LLMResponse(content='{"claims":[{"text":"durable fact","importance":0.8}]}')

    monkeypatch.setattr(LLMClient, "_chat_accounted", provider)
    journal = _journal(tmp_path)
    await _completed_session(journal, "source", "a useful trigger")
    manager = _manager(tmp_path, distillation_window=1)
    manager.distiller = Distiller(LLMConfig(model_name="openai/test", max_tokens=100))
    original_save = manager._save_distillation

    async def fail_claim_checkpoint() -> None:
        if manager._pending_batch is not None and "claims" in manager._pending_batch:
            raise OSError("claim checkpoint gap")
        await original_save()

    monkeypatch.setattr(manager, "_save_distillation", fail_claim_checkpoint)
    store = ProgramStore(tmp_path / "program.sqlite", build_id="test")
    with (
        bind_execution(ExecutionScope(store, "random-maintenance-1", 1000, "output")),
        pytest.raises(OSError),
    ):
        await manager.maybe_distill_completed_sessions(journal)
    restarted = _manager(tmp_path, distillation_window=1)
    restarted.distiller = Distiller(LLMConfig(model_name="openai/test", max_tokens=100))
    with bind_execution(ExecutionScope(store, "random-maintenance-2", 1000, "output")):
        assert await restarted.maybe_distill_completed_sessions(journal) == 1
    assert provider_calls == 1
    assert len(await restarted.claims.get_all_claims()) == 1


async def test_digest_bounds_projected_content_with_source_references(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    await _completed_session(journal, "source", "t" * 1000, reply="r" * 2000)
    manager = _manager(tmp_path)
    digest = await manager.get_memory_digest(journal)
    item = digest["recent_interactions"][0]
    assert item["session_id"] == "source"
    assert len(item["trigger_content"]) == 300
    assert len(item["llm_responses"][0]) == 600


async def test_unknown_model_outcome_blocks_repeated_distillation(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import pytest

    from tlon.agents.program_store import ProgramStore
    from tlon.base.config import LLMConfig
    from tlon.base.execution import ExecutionScope, UnknownModelOutcomeError, bind_execution
    from tlon.base.llm_client import LLMClient

    provider_calls = 0

    async def provider(self: LLMClient, *args: Any, **kwargs: Any) -> LLMResponse:
        nonlocal provider_calls
        provider_calls += 1
        raise OSError("provider response lost")

    monkeypatch.setattr(LLMClient, "_chat_accounted", provider)
    journal = _journal(tmp_path)
    await _completed_session(journal, "source", "do not silently consume")
    store = ProgramStore(tmp_path / "program.sqlite", build_id="test")
    for index in range(2):
        manager = _manager(tmp_path, distillation_window=1)
        manager.distiller = Distiller(LLMConfig(model_name="openai/test", max_tokens=100))
        with (
            bind_execution(ExecutionScope(store, f"outer:{index}", 1000, "output")),
            pytest.raises(UnknownModelOutcomeError),
        ):
            await manager.maybe_distill_completed_sessions(journal)
        assert not manager._consumed
        assert await manager.claims.get_all_claims() == []
    assert provider_calls == 1


async def test_reflection_program_provenance_is_not_a_distillation_receipt(tmp_path: Path) -> None:
    """Reflection may cite a run without consuming it for batch distillation."""
    journal = _journal(tmp_path)
    await _completed_session(journal, "old-session", "important new interaction")

    class ProgramReader:
        async def list_completed(self, started_after: datetime | None = None) -> list[str]:
            return ["program:run"]

        async def count_completed(self, started_after: datetime | None = None) -> int:
            return 1

        async def read_events(self, session_id: str) -> list[dict[str, Any]]:
            return await journal.read_events("old-session")

    sources = ProgramReader()
    manager = _manager(tmp_path, distillation_window=1)
    await manager.remember_fact(
        "reflection observation", claim_id="reflection:batch:0", source_session_ids=["program:run"]
    )
    restarted = _manager(tmp_path, distillation_window=1)
    digest = await restarted.get_memory_digest(sources)
    assert digest["undistilled_session_count"] == 1
    assert await restarted.maybe_distill_completed_sessions(sources) == 1
    assert len(await restarted.claims.get_all_claims()) == 2
    again = _manager(tmp_path, distillation_window=1)
    assert await again.maybe_distill_completed_sessions(sources) is None
    assert (await again.claims.get_claim("reflection:batch:0"))["source_session_ids"] == [
        "program:run"
    ]


async def test_new_remember_provenance_does_not_consume_old_session(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    await _completed_session(journal, "old-session", "unfinished memory processing")
    manager = _manager(tmp_path, distillation_window=1)
    await manager.remember_fact("reflection cited this", source_session_ids=["old-session"])
    restarted = _manager(tmp_path, distillation_window=1)
    assert (await restarted.get_memory_digest(journal))["undistilled_session_count"] == 1
    assert await restarted.maybe_distill_completed_sessions(journal) == 1
    claims = await restarted.claims.get_all_claims()
    assert [claim["origin"] for claim in claims] == ["remember", "distillation"]


async def test_corrupt_distillation_isolated_without_destroying_claims(tmp_path: Path) -> None:
    from unittest.mock import AsyncMock, Mock

    import pytest

    from tlon.base.maintenance import MaintenanceCheckpointError

    for index, payload in enumerate(
        (
            b'{"pending":',
            b"\xff",
            b"[]",
            b'{"consumed_source_ids": "bad", "pending": null}',
            b'{"consumed_source_ids": [], "pending": {"id": "x", "source_ids": [], "projections": 3}}',
            b'{"consumed_source_ids": [], "pending": {"id": "x", "source_ids": ["a"], "projections": []}}',
        )
    ):
        home = tmp_path / str(index)
        manager = _manager(home, distillation_window=1)
        await manager.load()
        await manager.remember_fact("existing fact")
        manager.distillation_file.write_bytes(payload)
        restarted = _manager(home, distillation_window=1)
        await restarted.load()
        assert restarted.maintenance_error is not None
        assert str(restarted.distillation_file) in restarted.maintenance_error
        assert (await restarted.claims.get_all_claims())[0]["text"] == "existing fact"
        await restarted.remember_fact("new fact")
        distiller = Mock(spec=Distiller)
        distiller.distill = AsyncMock(side_effect=AssertionError("must not call model"))
        restarted.distiller = distiller
        assert len(await restarted.claims.get_all_claims()) == 2
        with pytest.raises(MaintenanceCheckpointError, match="distillation.json"):
            await restarted.maybe_distill_completed_sessions(_journal(home))
        distiller.distill.assert_not_awaited()
        assert restarted.distillation_file.read_bytes() == payload
        assert restarted._pending_batch is None
        assert not restarted._consumed
        manager.distillation_file.write_text(
            json.dumps({"consumed_source_ids": [], "pending": None})
        )
        repaired = _manager(home, distillation_window=1)
        await repaired.load()
        assert repaired.maintenance_error is None
        assert await repaired.maybe_distill_completed_sessions(_journal(home)) is None


async def test_distillation_checkpoint_io_failure_is_not_empty_progress(tmp_path: Path) -> None:
    import pytest

    manager = _manager(tmp_path)
    manager.distillation_file.mkdir()
    with pytest.raises(IsADirectoryError):
        await manager.load()
