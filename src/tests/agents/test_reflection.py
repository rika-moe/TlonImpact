"""Reflection delete_fact operations (issue ).

Pins the fix: a reflection ``delete_fact`` carrying a ``fact_id`` must
delete that fact by ID — exact, or the unique ID prefix the reflection
prompt shows the model. An unknown ID must log a warning instead of
silently no-op'ing (the old path fed the uuid into keyword substring
matching, which could never match any fact text).
"""

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from tlon.agents.edn import Form
from tlon.agents.member_spec import MemberLoop, MemberRule, ReflectSpec
from tlon.agents.reflection import ReflectionEngine
from tlon.agents.session_journal import SessionJournal
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


class TestDeleteFact:
    async def test_delete_fact_by_full_id(self, tmp_path: Path) -> None:
        engine, manager = _engine(tmp_path)
        victim_id = await manager.remember_fact("用户偏好深色主题")
        keeper_id = await manager.remember_fact("项目使用 Python")

        await engine._apply_operations([{"action": "delete_fact", "fact_id": victim_id}])

        assert await manager.episodic.get_fact(victim_id) is None
        assert await manager.episodic.get_fact(keeper_id) is not None

    async def test_delete_fact_by_id_prefix(self, tmp_path: Path) -> None:
        """The reflection prompt shows facts by ID prefix, so the model's
        fact_id is normally a prefix — it must still resolve."""
        engine, manager = _engine(tmp_path)
        victim_id = await manager.remember_fact("过时的部署地址")

        await engine._apply_operations(
            [{"action": "delete_fact", "fact_id": victim_id[:8]}]
        )

        assert await manager.episodic.get_fact(victim_id) is None

    async def test_delete_fact_unknown_id_warns_and_deletes_nothing(
        self, tmp_path: Path
    ) -> None:
        engine, manager = _engine(tmp_path)
        keeper_id = await manager.remember_fact("仍然有效的事实")

        with patch("tlon.memory.manager.logger") as mock_logger:
            await engine._apply_operations(
                [{"action": "delete_fact", "fact_id": "no-such-fact-id"}]
            )

        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args.args[0] == "fact_delete_not_found"
        assert await manager.episodic.get_fact(keeper_id) is not None

    async def test_delete_fact_by_description_falls_back_to_keyword_match(
        self, tmp_path: Path
    ) -> None:
        engine, manager = _engine(tmp_path)
        victim_id = await manager.remember_fact("用户喜欢浓缩咖啡")

        await engine._apply_operations(
            [{"action": "delete_fact", "description": "浓缩咖啡"}]
        )

        assert await manager.episodic.get_fact(victim_id) is None

    async def test_delete_fact_without_target_warns(self, tmp_path: Path) -> None:
        engine, _manager = _engine(tmp_path)

        with patch("tlon.agents.reflection.logger") as mock_logger:
            await engine._apply_operations([{"action": "delete_fact"}])

        mock_logger.warning.assert_called_once()
        assert mock_logger.warning.call_args.args[0] == "reflection_delete_fact_no_target"


class TestRunEndToEnd:
    async def test_run_applies_llm_delete_fact(self, tmp_path: Path) -> None:
        """Full _run path: the digest lists facts, the LLM answers with a
        delete_fact carrying the prompt-visible prefix, and the fact is
        really gone from the store."""
        manager = MemoryManager("agent1", MemoryConfig(), data_dir=tmp_path / "agent1")
        victim_id = await manager.remember_fact("已经作废的决定")
        response = json.dumps(
            {
                "analysis": "发现过时事实",
                "operations": [{"action": "delete_fact", "fact_id": victim_id[:8]}],
            },
            ensure_ascii=False,
        )
        journal = SessionJournal("agent1", TlonPaths(home=tmp_path))
        engine = ReflectionEngine(manager, cast(LLMClient, _FakeLLM(response)), _rule(), journal)

        await engine._run()

        assert await manager.episodic.get_fact(victim_id) is None
