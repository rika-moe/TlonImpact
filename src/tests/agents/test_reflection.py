"""Reflection delete_claim operations (issue , ADR 0017).

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

from tlon.agents.member_spec import MemberLoop, MemberRule, ReflectSpec
from tlon.agents.reflection import ReflectionEngine
from tlon.agents.session_journal import SessionJournal
from tlon.base.llm_client import LLMClient, LLMResponse
from tlon.base.paths import TlonPaths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlosure.edn import Form


class _FakeLLM:
    """Duck-typed LLMClient returning one canned response."""

    def __init__(self, content: str) -> None:
        self._content = content

    async def chat(self, **kwargs: Any) -> LLMResponse:
        return LLMResponse(content=self._content)


def _rule() -> MemberRule:
    return MemberRule(
        name="test",
        on=None,
        when=None,
        loop=MemberLoop(
            context_sections=(),
            stages=(),
            until=Form(),
            max_iterations=1,
            max_consecutive_errors=1,
            reflect=ReflectSpec(enabled=True, interval_minutes=0),
        ),
        actions=(),
    )


def _engine(tmp_path: Path, llm_content: str = "{}") -> tuple[ReflectionEngine, MemoryManager]:
    manager = MemoryManager("agent1", MemoryConfig(), data_dir=tmp_path / "agent1")
    journal = SessionJournal("agent1", TlonPaths(home=tmp_path))
    engine = ReflectionEngine(manager, cast(LLMClient, _FakeLLM(llm_content)), _rule(), journal)
    return engine, manager


class TestDeleteClaim:
    async def test_delete_claim_by_full_id(self, tmp_path: Path) -> None:
        engine, manager = _engine(tmp_path)
        victim_id = await manager.remember_fact("用户偏好深色主题")
        keeper_id = await manager.remember_fact("项目使用 Python")

        await engine._apply_operations([{"action": "delete_claim", "claim_id": victim_id}])

        assert await manager.claims.get_claim(victim_id) is None
        assert await manager.claims.get_claim(keeper_id) is not None

    async def test_delete_claim_by_id_prefix(self, tmp_path: Path) -> None:
        """The reflection prompt shows claims by ID prefix, so the model's
        claim_id is normally a prefix — it must still resolve."""
        engine, manager = _engine(tmp_path)
        victim_id = await manager.remember_fact("过时的部署地址")

        await engine._apply_operations(
            [{"action": "delete_claim", "claim_id": victim_id[:8]}]
        )

        assert await manager.claims.get_claim(victim_id) is None

    async def test_delete_claim_unknown_id_warns_and_deletes_nothing(
        self, tmp_path: Path
    ) -> None:
        engine, manager = _engine(tmp_path)
        keeper_id = await manager.remember_fact("仍然有效的记忆")

        with patch("tlon.memory.manager.logger") as mock_logger:
            await engine._apply_operations(
                [{"action": "delete_claim", "claim_id": "no-such-fact-id"}]
            )

        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args.args[0] == "claim_delete_not_found"
        assert await manager.claims.get_claim(keeper_id) is not None

    async def test_delete_claim_by_description_falls_back_to_keyword_match(
        self, tmp_path: Path
    ) -> None:
        engine, manager = _engine(tmp_path)
        victim_id = await manager.remember_fact("用户喜欢浓缩咖啡")

        await engine._apply_operations(
            [{"action": "delete_claim", "description": "浓缩咖啡"}]
        )

        assert await manager.claims.get_claim(victim_id) is None

    async def test_delete_claim_without_target_warns(self, tmp_path: Path) -> None:
        engine, _manager = _engine(tmp_path)

        with patch("tlon.agents.reflection.logger") as mock_logger:
            await engine._apply_operations([{"action": "delete_claim"}])

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
        engine = ReflectionEngine(manager, cast(LLMClient, _FakeLLM(response)), _rule(), journal)

        await engine._run()

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
    """The reflection task is tracked, not free-floating (issue ).

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
        engine = ReflectionEngine(manager, cast(LLMClient, llm), _rule(), journal)
        for _ in range(5):  # reflection_min_interactions default
            engine.note_interaction()
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
