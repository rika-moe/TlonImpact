"""Tests for the compact tool and summary instructions passthrough."""

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.base.config import LLMConfig
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.memory.summarizer import Summarizer
from tlon.tools.memory.compact import CompactTool


def _make_interactions() -> list[dict[str, Any]]:
    return [
        {
            "id": "i-001",
            "message": {"sender_name": "用户", "content": "帮我看看部署方案"},
            "response": {"content": "好的，我建议用蓝绿部署"},
            "tool_results": [],
        }
    ]


class TestCompactTool:
    @pytest.fixture
    def memory(self) -> MagicMock:
        mock = MagicMock()
        mock.generate_summary = AsyncMock(return_value="summary-abc123")
        return mock

    @pytest.fixture
    def tool(self, memory: MagicMock) -> CompactTool:
        return CompactTool(memory)

    async def test_compact_without_instructions(
        self, tool: CompactTool, memory: MagicMock
    ) -> None:
        result = await tool.execute()

        assert result.success is True
        assert result.data["summary_id"] == "summary-abc123"
        memory.generate_summary.assert_awaited_once_with(force=True, instructions=None)

    async def test_compact_with_instructions(
        self, tool: CompactTool, memory: MagicMock
    ) -> None:
        result = await tool.execute(instructions="保留部署方案的决策")

        assert result.success is True
        memory.generate_summary.assert_awaited_once_with(
            force=True, instructions="保留部署方案的决策"
        )

    async def test_compact_no_content(self, tool: CompactTool, memory: MagicMock) -> None:
        memory.generate_summary = AsyncMock(return_value=None)

        result = await tool.execute()

        assert result.success is True
        assert result.data == {}
        assert "没有足够的内容" in result.message

    async def test_compact_failure(self, tool: CompactTool, memory: MagicMock) -> None:
        memory.generate_summary = AsyncMock(side_effect=RuntimeError("boom"))

        result = await tool.execute()

        assert result.success is False
        assert "boom" in result.error

    def test_tool_schema(self, tool: CompactTool) -> None:
        assert tool.name == "compact"
        props = tool.parameters["properties"]
        assert "instructions" in props
        assert "force" not in props
        assert "required" not in tool.parameters


class TestManagerInstructionsPassthrough:
    """MemoryManager forwards instructions to the summarizer."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> MemoryManager:
        with patch("tlon.memory.manager.paths") as mock_paths:
            mock_paths.agent_memory_dir.return_value = tmp_path
            mock_paths.memory_config_file.return_value = tmp_path / "memory.yaml"
            manager = MemoryManager("test-agent", MemoryConfig())

        manager._loaded = True
        manager.working = MagicMock()
        manager.working.get_all = AsyncMock(return_value=_make_interactions())
        manager.working.remove_by_ids = AsyncMock(return_value=1)
        manager.episodic = MagicMock()
        manager.episodic.get_recent_facts = AsyncMock(return_value=[])
        manager.episodic.add_summary = AsyncMock(return_value="sum-001")
        manager.episodic.add_fact = AsyncMock()
        manager.summarizer = MagicMock()
        manager.summarizer.process_interactions = AsyncMock(
            return_value=(
                {
                    "timestamp": "2026-07-22T00:00:00",
                    "session_id": "s-001",
                    "summary_text": "讨论了部署方案",
                },
                [],
            )
        )
        return manager

    async def test_instructions_forwarded_to_summarizer(
        self, manager: MemoryManager
    ) -> None:
        summary_id = await manager.generate_summary(
            force=True, instructions="保留部署方案的决策"
        )

        assert summary_id == "sum-001"
        manager.summarizer.process_interactions.assert_awaited_once()
        kwargs = manager.summarizer.process_interactions.call_args.kwargs
        assert kwargs["instructions"] == "保留部署方案的决策"

    async def test_auto_path_passes_no_instructions(
        self, manager: MemoryManager
    ) -> None:
        summary_id = await manager.generate_summary(force=True)

        assert summary_id == "sum-001"
        kwargs = manager.summarizer.process_interactions.call_args.kwargs
        assert kwargs["instructions"] is None


class TestSummarizerInstructionsPrompt:
    """Summarizer injects instructions into the summary prompt."""

    @pytest.fixture
    def summarizer(self) -> Summarizer:
        s = Summarizer(LLMConfig(model_name="test-model"))
        response = MagicMock()
        response.content = '{"summary": "摘要", "key_decisions": [], "action_items": []}'
        s.llm_client.chat = AsyncMock(return_value=response)
        return s

    async def test_instructions_included_in_prompt(self, summarizer: Summarizer) -> None:
        summary, _, _ = await summarizer.generate_summary(
            _make_interactions(), instructions="保留部署方案的决策"
        )

        assert summary == "摘要"
        system_prompt = summarizer.llm_client.chat.call_args.kwargs["system_prompt"]
        assert "请特别保留/关注以下内容：保留部署方案的决策" in system_prompt

    async def test_no_instructions_prompt_unchanged(self, summarizer: Summarizer) -> None:
        await summarizer.generate_summary(_make_interactions())

        system_prompt = summarizer.llm_client.chat.call_args.kwargs["system_prompt"]
        assert "请特别保留" not in system_prompt

    async def test_process_interactions_forwards_instructions(
        self, summarizer: Summarizer
    ) -> None:
        await summarizer.process_interactions(
            _make_interactions(),
            session_id="s-001",
            instructions="保留部署方案的决策",
        )

        # First chat call is the summary call
        system_prompt = summarizer.llm_client.chat.call_args_list[0].kwargs[
            "system_prompt"
        ]
        assert "请特别保留/关注以下内容：保留部署方案的决策" in system_prompt
