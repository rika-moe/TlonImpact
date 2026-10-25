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
from unittest.mock import AsyncMock, patch

import pytest

from tests.history_helpers import append_history
from tlon.agents.member_spec import ReflectSpec
from tlon.agents.reflection import ReflectionEngine
from tlon.agents.run_journal import RunJournal
from tlon.base.config import LLMConfig
from tlon.base.execution import ExecutionScope, ModelRequestRejectedError, UnknownModelOutcomeError
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
    journal = RunJournal("agent1", TlonPaths(home=tmp_path))
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
        journal = RunJournal("agent1", TlonPaths(home=tmp_path))
        engine = ReflectionEngine(
            manager,
            cast(LLMClient, _FakeLLM(response)),
            ReflectSpec(enabled=True, interval_minutes=0),
            journal,
        )

        for index in range(5):
            await append_history(engine._journal, str(index), "run/start", trigger_content="hello")
            await append_history(engine._journal, str(index), "run/end", reason="complete")
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
    runtime's idle accounting reads this). Shutdown drains it within a
    bounded window before cancellation, without leaving an orphaned coroutine.
    """

    async def _trigger(self, tmp_path: Path) -> tuple[ReflectionEngine, _BlockingLLM]:
        """Trigger a reflection that blocks inside the LLM call."""
        manager = MemoryManager("agent1", MemoryConfig(), data_dir=tmp_path / "agent1")
        await manager.remember_fact("一条供反思审视的记忆")
        journal = RunJournal("agent1", TlonPaths(home=tmp_path))
        llm = _BlockingLLM()
        engine = ReflectionEngine(
            manager, cast(LLMClient, llm), ReflectSpec(enabled=True, interval_minutes=0), journal
        )
        for _ in range(5):  # reflection_min_interactions default
            await append_history(engine._journal, str(_), "run/start", trigger_content="hello")
            await append_history(engine._journal, str(_), "run/end", reason="complete")
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

    async def test_shutdown_cancels_in_flight_reflection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ReflectionEngine, "SHUTDOWN_GRACE_SECONDS", 0.02)
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
        await append_history(engine._journal, str(index), "run/start", trigger_content="hello")
        await append_history(engine._journal, str(index), "run/end", reason="complete")
    await engine.check_trigger()
    task = engine._task
    assert task is not None
    await task
    assert observed == [True, False]


async def _complete_sources(engine: ReflectionEngine, count: int = 5) -> None:
    for index in range(count):
        await append_history(engine._journal, str(index), "run/start", trigger_content="hello")
        await append_history(engine._journal, str(index), "run/end", reason="complete")


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
            "undistilled_run_count": 1,
            "recent_interactions": [{"run_id": "run-1", "trigger_content": "source text"}],
        }
    )
    assert "run-1" in prompt
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


async def _durable_blocking_reflection(
    tmp_path: Path,
) -> tuple[ReflectionEngine, asyncio.Event, AsyncMock, str]:
    engine, manager = _engine(tmp_path)
    await manager.remember_fact("a verified claim for reflection")
    await _complete_sources(engine)
    store = engine._journal._program_store
    assert store is not None
    client = LLMClient(
        LLMConfig(model_name="openai/test", max_output_tokens=32, context_window=100000)
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def provider(*args: Any) -> LLMResponse:
        entered.set()
        await release.wait()
        return LLMResponse(content='{"operations": []}', usage={"completion_tokens": 2})

    request = AsyncMock(side_effect=provider)
    client._chat_with_retries = request
    engine._llm_client = client
    engine._execution_factory = lambda purpose: ExecutionScope(
        store, "maintenance:" + purpose, 100, "output"
    )
    await engine.check_trigger()
    await asyncio.wait_for(entered.wait(), 2)
    root_id = "maintenance:reflection:" + engine._load_checkpoint()["pending"]["id"]
    return engine, release, request, root_id


async def test_shutdown_drains_provider_commit_and_checkpoint_before_closing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ReflectionEngine, "SHUTDOWN_GRACE_SECONDS", 0.5)
    engine, release, request, root_id = await _durable_blocking_reflection(tmp_path)
    closing = asyncio.create_task(engine.shutdown())
    await asyncio.sleep(0.02)
    release.set()
    await asyncio.wait_for(closing, 1)
    store = engine._journal._program_store
    assert store is not None
    assert store.budget_usage(root_id)["pending_count"] == 0
    assert store.budget_usage(root_id)["used"] == 2
    assert engine._load_checkpoint()["pending"] is None
    assert engine.diagnostic_error is None
    request.assert_awaited_once()
    await engine.check_trigger()
    assert engine._task is None


async def test_shutdown_timeout_preserves_unknown_reservation_and_restart_never_resends(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ReflectionEngine, "SHUTDOWN_GRACE_SECONDS", 0.02)
    engine, _release, request, root_id = await _durable_blocking_reflection(tmp_path)
    await engine.shutdown()
    store = engine._journal._program_store
    assert store is not None
    assert store.budget_usage(root_id)["pending_count"] == 1
    assert store.budget_usage(root_id)["used"] == 32
    state = engine._load_checkpoint()
    assert state["pending"] is not None and state["seen"] == []
    assert "manual reconciliation required" in (engine.diagnostic_error or "")
    assert "keep reservation" in (engine.diagnostic_error or "")
    assert not engine.is_running
    # Rebuilding checks the same durable request identity and fixed budget, not a new root.
    restarted, _ = _engine(tmp_path)
    restarted._llm_client = engine._llm_client
    restarted._execution_factory = engine._execution_factory
    await restarted.check_trigger()
    assert restarted._task is not None
    await restarted._task
    assert "UnknownModelOutcomeError" in (restarted.diagnostic_error or "")
    assert "manual reconciliation required" in (restarted.diagnostic_error or "")
    with patch("tlon.base.maintenance.monotonic", return_value=1e12):
        for _ in range(3):
            await restarted.check_trigger()
    request.assert_awaited_once()
    assert restarted._load_checkpoint() == state
    assert store.budget_usage(root_id)["pending_count"] == 1


async def test_shutdown_closes_admission_even_if_check_trigger_is_suspended(tmp_path: Path) -> None:
    engine, _ = _engine(tmp_path)
    await _complete_sources(engine)
    entered, release = asyncio.Event(), asyncio.Event()
    original = engine._journal.list_completed

    async def list_sources() -> list[str]:
        entered.set()
        await release.wait()
        return await original()

    with patch.object(engine._journal, "list_completed", side_effect=list_sources):
        checking = asyncio.create_task(engine.check_trigger())
        await entered.wait()
        await engine.shutdown()
        release.set()
        await checking
    await engine.check_trigger()
    assert engine._task is None and not engine.is_running


async def test_empty_boot_sources_are_consumed_without_starting_reflection(tmp_path: Path) -> None:
    engine, manager = _engine(tmp_path)
    await manager.remember_fact("existing memory does not make inert boot meaningful")
    from tlon.agents.program_store import ProgramStore

    store = ProgramStore(engine._journal._program_database)
    engine._journal._program_store = store
    sources = [f"run-{index}:handler:boot" for index in range(5)]
    for source in sources:
        store.ensure_entry(source, "{}", "{:payload nil}", {})
        store.complete_entry(source, None)
    with patch.object(
        engine._llm_client, "chat", side_effect=AssertionError("inert boot requested model")
    ) as request:
        await engine.check_trigger()
        assert engine._task is None
        await engine.check_trigger()
        request.assert_not_awaited()
    assert engine._load_checkpoint()["seen"] == sources
    assert engine._load_checkpoint()["pending"] is None
