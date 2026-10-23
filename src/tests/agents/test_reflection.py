"""Reflection delete_claim operations (ADR 0017).

Pins the fix: a reflection ``delete_claim`` carrying a ``claim_id`` must
delete that claim by ID — exact, or the unique ID prefix the reflection
prompt shows the model. An unknown ID must log a warning instead of
silently no-op'ing (the old path fed the uuid into keyword substring
matching, which could never match any claim text).
"""

import asyncio
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

from tests.history_helpers import append_history
from tlon.agents.member_spec import ReflectSpec
from tlon.agents.reflection import ReflectionEngine
from tlon.agents.session_journal import SessionJournal
from tlon.base.execution import ModelRequestRejectedError, UnknownModelOutcomeError
from tlon.base.llm_client import LLMClient, LLMResponse
from tlon.base.paths import TlonPaths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig


class _FakeLLM:
    """Duck-typed LLMClient returning one canned response."""

    def __init__(self, content: str) -> None:
        self._content = content

    async def chat(self, **kwargs: Any) -> LLMResponse:
        return LLMResponse(content=self._content)


def _engine(tmp_path: Path, llm_content: str = "{}") -> tuple[ReflectionEngine, MemoryManager]:
    manager = MemoryManager("agent1", MemoryConfig(), data_dir=tmp_path / "agent1")
    journal = SessionJournal("agent1", TlonPaths(home=tmp_path))
    engine = ReflectionEngine(
        manager,
        cast(LLMClient, _FakeLLM(llm_content)),
        ReflectSpec(enabled=True, interval_minutes=0),
        journal,
    )
    return engine, manager


async def _apply_operations(engine: ReflectionEngine, operations: list[dict[str, Any]]) -> None:
    for operation in await engine._freeze_operations(
        operations, await engine._memory.claims.get_all_claims()
    ):
        await engine._apply_operation(operation)


class TestDeleteClaim:
    async def test_delete_claim_by_full_id(self, tmp_path: Path) -> None:
        engine, manager = _engine(tmp_path)
        victim_id = await manager.remember_fact("用户偏好深色主题")
        keeper_id = await manager.remember_fact("项目使用 Python")

        await _apply_operations(engine, [{"action": "delete_claim", "claim_id": victim_id}])

        assert await manager.claims.get_claim(victim_id) is None
        assert await manager.claims.get_claim(keeper_id) is not None

    async def test_delete_claim_by_id_prefix(self, tmp_path: Path) -> None:
        """The reflection prompt shows claims by ID prefix, so the model's
        claim_id is normally a prefix — it must still resolve."""
        engine, manager = _engine(tmp_path)
        victim_id = await manager.remember_fact("过时的部署地址")

        await _apply_operations(engine, [{"action": "delete_claim", "claim_id": victim_id[:8]}])

        assert await manager.claims.get_claim(victim_id) is None

    async def test_delete_claim_unknown_id_warns_and_deletes_nothing(self, tmp_path: Path) -> None:
        engine, manager = _engine(tmp_path)
        keeper_id = await manager.remember_fact("仍然有效的记忆")

        with patch("tlon.agents.reflection.logger") as mock_logger:
            await _apply_operations(
                engine, [{"action": "delete_claim", "claim_id": "no-such-fact-id"}]
            )

        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args.args[0] == "reflection_delete_claim_unresolved"
        assert await manager.claims.get_claim(keeper_id) is not None

    async def test_delete_claim_by_description_falls_back_to_keyword_match(
        self, tmp_path: Path
    ) -> None:
        engine, manager = _engine(tmp_path)
        victim_id = await manager.remember_fact("用户喜欢浓缩咖啡")

        await _apply_operations(engine, [{"action": "delete_claim", "description": "浓缩咖啡"}])

        assert await manager.claims.get_claim(victim_id) is None

    async def test_delete_claim_without_target_warns(self, tmp_path: Path) -> None:
        engine, _manager = _engine(tmp_path)

        with patch("tlon.agents.reflection.logger") as mock_logger:
            await _apply_operations(engine, [{"action": "delete_claim"}])

        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args.args[0] == "reflection_delete_claim_no_target"


class TestRunEndToEnd:
    async def test_run_applies_llm_delete_claim(self, tmp_path: Path) -> None:
        """Full _run path: the digest lists claims, the LLM answers with a
        delete_claim carrying the prompt-visible prefix, and the claim is
        really gone from the store."""
        manager = MemoryManager("agent1", MemoryConfig(), data_dir=tmp_path / "agent1")
        victim_id = await manager.remember_fact("已经作废的决定")
        response = json.dumps(
            {
                "analysis": "发现过时记忆",
                "operations": [{"action": "delete_claim", "claim_id": victim_id[:8]}],
            },
            ensure_ascii=False,
        )
        journal = SessionJournal("agent1", TlonPaths(home=tmp_path))
        engine = ReflectionEngine(
            manager,
            cast(LLMClient, _FakeLLM(response)),
            ReflectSpec(enabled=True, interval_minutes=0),
            journal,
        )

        for index in range(5):
            await append_history(
                engine._journal, str(index), "session/start", trigger_content="hello"
            )
            await append_history(engine._journal, str(index), "session/end", reason="complete")
        await engine.check_trigger()
        assert engine._task is not None
        await engine._task

        assert await manager.claims.get_claim(victim_id) is None


class _BlockingLLM:
    """Duck-typed LLMClient whose chat blocks until released."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.started.set()
        await self.release.wait()
        return LLMResponse(content='{"operations": []}')


class TestLifecycle:
    """The reflection task is tracked, not free-floating
    (docs/design/runtime.md).

    An in-flight reflection reports itself as running (the member
    runtime's idle accounting reads this), and ``shutdown`` cancels it
    instead of leaving an orphaned coroutine.
    """

    async def _trigger(self, tmp_path: Path) -> tuple[ReflectionEngine, _BlockingLLM]:
        """Trigger a reflection that blocks inside the LLM call."""
        manager = MemoryManager("agent1", MemoryConfig(), data_dir=tmp_path / "agent1")
        await manager.remember_fact("一条供反思审视的记忆")
        journal = SessionJournal("agent1", TlonPaths(home=tmp_path))
        llm = _BlockingLLM()
        engine = ReflectionEngine(
            manager, cast(LLMClient, llm), ReflectSpec(enabled=True, interval_minutes=0), journal
        )
        for _ in range(5):  # reflection_min_interactions default
            await append_history(engine._journal, str(_), "session/start", trigger_content="hello")
            await append_history(engine._journal, str(_), "session/end", reason="complete")
        await engine.check_trigger()
        await asyncio.wait_for(llm.started.wait(), timeout=2)
        return engine, llm

    async def test_in_flight_reflection_reports_running(self, tmp_path: Path) -> None:
        engine, llm = await self._trigger(tmp_path)
        assert engine.is_running
        task = engine._task
        assert task is not None and not task.done()

        llm.release.set()
        await task

        assert not engine.is_running
        assert engine._task is None

    async def test_shutdown_cancels_in_flight_reflection(self, tmp_path: Path) -> None:
        engine, _llm = await self._trigger(tmp_path)
        task = engine._task
        assert task is not None

        await engine.shutdown()

        assert task.cancelled()
        assert engine._task is None
        assert not engine.is_running

    async def test_shutdown_without_reflection_is_noop(self, tmp_path: Path) -> None:
        engine, _manager = _engine(tmp_path)

        await engine.shutdown()  # never triggered: nothing to cancel

        assert engine._task is None
        assert not engine.is_running


class TestPromptDataFile:
    """The reflection system prompt is a package data file, not a code
    constant (policy text, readable and editable without touching the
    engine)."""

    def test_prompt_loaded_from_data_file(self) -> None:
        from importlib import resources

        from tlon.agents.reflection import REFLECTION_SYSTEM_PROMPT

        on_disk = (
            resources.files("tlon.agents")
            .joinpath("prompts/reflection_system.md")
            .read_text(encoding="utf-8")
            .strip()
        )
        assert on_disk == REFLECTION_SYSTEM_PROMPT
        # The operation vocabulary the parser understands must be named
        # in the prompt — a drift here means the LLM is asked for
        # operations the engine cannot apply.
        for action in ("record_claim", "delete_claim", "noop"):
            assert action in REFLECTION_SYSTEM_PROMPT


async def test_reflection_notifies_active_and_waiting(tmp_path: Path) -> None:
    engine, manager = _engine(tmp_path)
    observed: list[bool] = []

    async def changed() -> None:
        observed.append(engine.is_running)

    engine._state_changed = changed
    for index in range(manager.config.reflection_min_interactions):
        await append_history(engine._journal, str(index), "session/start", trigger_content="hello")
        await append_history(engine._journal, str(index), "session/end", reason="complete")
    await engine.check_trigger()
    task = engine._task
    assert task is not None
    await task
    assert observed == [True, False]


async def _complete_sources(engine: ReflectionEngine, count: int = 5) -> None:
    for index in range(count):
        await append_history(engine._journal, str(index), "session/start", trigger_content="hello")
        await append_history(engine._journal, str(index), "session/end", reason="complete")


async def test_reflection_completed_sources_survive_restart_and_deduplicate(tmp_path: Path) -> None:
    engine, manager = _engine(
        tmp_path, '{"operations": [{"action": "record_claim", "description": "durable"}]}'
    )
    await _complete_sources(engine)
    await engine.check_trigger()
    task = engine._task
    assert task is not None
    await task
    assert len(await manager.claims.get_all_claims()) == 1
    restarted, _ = _engine(
        tmp_path, '{"operations": [{"action": "record_claim", "description": "duplicate"}]}'
    )
    await restarted.check_trigger()
    assert restarted._task is None
    assert len(await manager.claims.get_all_claims()) == 1
    assert len(restarted._load_checkpoint()["seen"]) == 5


async def test_reflection_failure_after_claim_append_reuses_frozen_batch(tmp_path: Path) -> None:
    engine, manager = _engine(
        tmp_path, '{"operations": [{"action": "record_claim", "description": "durable"}]}'
    )
    await _complete_sources(engine)
    original = manager.remember_fact

    async def crash_after_append(*args: Any, **kwargs: Any) -> str:
        await original(*args, **kwargs)
        raise OSError("crash after append")

    with patch.object(manager, "remember_fact", side_effect=crash_after_append):
        await engine.check_trigger()
        task = engine._task
        assert task is not None
        await task
    checkpoint = engine._load_checkpoint()
    assert checkpoint["seen"] == []
    assert checkpoint["pending"]["next"] == 0
    restarted, restarted_memory = _engine(tmp_path, "model must not be requested again")
    with patch.object(
        restarted._llm_client, "chat", side_effect=AssertionError("duplicate model call")
    ):
        await restarted.check_trigger()
        task = restarted._task
        assert task is not None
        await task
    claims = await restarted_memory.claims.get_all_claims()
    assert len(claims) == 1
    assert claims[0]["text"] == "durable"
    assert restarted._load_checkpoint()["pending"] is None


async def test_reflection_prompt_includes_undistilled_program_interactions(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    prompt = engine._build_prompt(
        {
            "claim_count": 0,
            "claims": [],
            "undistilled_session_count": 1,
            "recent_interactions": [
                {"session_id": "program:run-1", "trigger_content": "source text"}
            ],
        }
    )
    assert "program:run-1" in prompt
    assert "source text" in prompt


@pytest.mark.parametrize(
    "content",
    ["{", "[]", '{"seen": []}', '{"seen": [], "last_completed": null, "pending": {"id": 1}}'],
)
async def test_damaged_reflection_checkpoint_is_preserved_and_reported(
    tmp_path: Path, content: str
) -> None:
    engine, _ = _engine(tmp_path)
    engine._checkpoint.write_text(content)
    with patch("tlon.agents.reflection.logger") as logger:
        for _ in range(3):
            await engine.check_trigger()
    assert engine._task is None
    assert engine._checkpoint.read_text() == content
    assert str(engine._checkpoint) in (engine.diagnostic_error or "")
    logger.warning.assert_called_once()
    # Repair is deliberate and a reload is required to retry a blocked batch.
    engine._checkpoint.write_text(json.dumps({"seen": [], "last_completed": None, "pending": None}))
    restarted, _ = _engine(tmp_path)
    await _complete_sources(restarted)
    await restarted.check_trigger()
    assert restarted._task is not None
    await restarted._task
    assert restarted.diagnostic_error is None
    assert restarted._load_checkpoint()["pending"] is None


@pytest.mark.parametrize(
    "error", [ModelRequestRejectedError("rejected"), UnknownModelOutcomeError("unknown")]
)
async def test_permanent_reflection_failure_does_not_resubmit(
    tmp_path: Path, error: Exception
) -> None:
    engine, manager = _engine(tmp_path)
    await manager.remember_fact("a claim")
    await _complete_sources(engine)
    with patch.object(engine._llm_client, "chat", side_effect=error) as chat:
        await engine.check_trigger()
        assert engine._task is not None
        await engine._task
        checkpoint = engine._checkpoint.read_bytes()
        with patch("tlon.base.maintenance.monotonic", return_value=1e12):
            for _ in range(5):
                await engine.check_trigger()
        assert chat.await_count == 1
    assert engine._task is None
    assert engine._checkpoint.read_bytes() == checkpoint
    assert type(error).__name__ in (engine.diagnostic_error or "")


async def test_transient_reflection_failure_retries_frozen_batch_after_backoff(
    tmp_path: Path,
) -> None:
    engine, manager = _engine(tmp_path)
    await manager.remember_fact("a claim")
    await _complete_sources(engine)
    with patch("tlon.base.maintenance.monotonic", return_value=100.0) as clock:
        with patch.object(engine._llm_client, "chat", side_effect=OSError("temporary")) as chat:
            await engine.check_trigger()
            assert engine._task is not None
            await engine._task
            pending = engine._load_checkpoint()["pending"]
            for now in [100.0, 110.0, 129.9]:
                clock.return_value = now
                await engine.check_trigger()
                assert engine._task is None
            assert chat.await_count == 1
            clock.return_value = 130.0
            await engine.check_trigger()
            assert engine._task is not None
            await engine._task
            assert chat.await_count == 2
            assert engine._load_checkpoint()["pending"] == pending
            clock.return_value = 189.9
            await engine.check_trigger()
            assert engine._task is None
        clock.return_value = 190.0
        await engine.check_trigger()
        assert engine._task is not None
        await engine._task
    assert engine._load_checkpoint()["pending"] is None
    assert engine.diagnostic_error is None
