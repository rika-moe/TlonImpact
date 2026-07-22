"""Tests for Agent prompt cache behavior.

Verifies that:
- _get_system_prompt() builds on first call
- _get_system_prompt() reuses cache while persona files are unchanged
- _get_system_prompt() rebuilds when a persona file (own or team) changes
- _build_knowledge_context() injects memory into user prompt
- Tool execution does not invalidate system prompt cache
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.agents.agent import Agent
from tlon.base.config import AgentConfig
from tlon.base.paths import TlonPaths


def _make_agent(team_members: list[AgentConfig] | None = None):
    config = AgentConfig(id="a-001", name="林知遥")
    tool_executor = MagicMock()
    tool_executor.get_tool_definitions.return_value = []

    memory = MagicMock()
    memory.build_context_blocks = AsyncMock(return_value=[])
    memory.check_idle_timeout = AsyncMock()

    event_bus = MagicMock()
    event_bus.publish = AsyncMock()

    return Agent(
        config=config,
        event_bus=event_bus,
        llm_client=MagicMock(),
        memory=memory,
        task_manager=MagicMock(),
        tool_executor=tool_executor,
        team_members=team_members,
    )


def _use_tmp_paths(monkeypatch, tmp_path) -> TlonPaths:
    """Redirect the paths singleton to a tmp dir (keeps tests hermetic)."""
    test_paths = TlonPaths(home=tmp_path)
    monkeypatch.setattr("tlon.agents.context_builder.paths", test_paths)
    return test_paths


class TestPromptCacheBasic:
    @pytest.mark.asyncio
    async def test_first_call_builds_prompt(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        agent = _make_agent()
        with patch.object(agent, "_build_prompt_context", new_callable=AsyncMock) as mock_ctx:
            mock_ctx.return_value = MagicMock()
            with patch("tlon.agents.context_builder.SystemPromptBuilder") as mock_builder_cls:
                mock_builder_cls.return_value.build.return_value = "built prompt"
                result = await agent._get_system_prompt()

        assert result == "built prompt"
        assert agent._system_prompt_cache == "built prompt"

    @pytest.mark.asyncio
    async def test_second_call_uses_cache(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        agent = _make_agent()
        agent._system_prompt_cache = "cached prompt"
        agent._system_prompt_mtime = agent._persona_mtime_signature(agent._persona_files())

        with patch.object(agent, "_build_prompt_context", new_callable=AsyncMock) as mock_ctx:
            result = await agent._get_system_prompt()
            mock_ctx.assert_not_called()

        assert result == "cached prompt"


class TestPromptCacheInvalidation:
    """Cache is keyed on persona file mtimes: edits take effect without restart."""

    @pytest.mark.asyncio
    async def test_rebuilds_when_own_soul_file_changes(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        soul_file = test_paths.agent_soul_file("a-001")
        soul_file.write_text("旧灵魂", encoding="utf-8")

        agent = _make_agent()
        prompt1 = await agent._get_system_prompt()
        assert "旧灵魂" in prompt1

        # Edit the file and bump mtime (guard against mtime resolution)
        soul_file.write_text("新灵魂", encoding="utf-8")
        new_mtime = soul_file.stat().st_mtime + 10
        os.utime(soul_file, (new_mtime, new_mtime))

        prompt2 = await agent._get_system_prompt()
        assert "新灵魂" in prompt2
        assert "旧灵魂" not in prompt2

    @pytest.mark.asyncio
    async def test_rebuilds_when_team_soul_file_changes(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        member = AgentConfig(id="b-002", name="队友")
        team_soul = test_paths.agent_soul_file("b-002")
        team_soul.write_text("团队旧设定", encoding="utf-8")

        agent = _make_agent(team_members=[member])
        prompt1 = await agent._get_system_prompt()
        assert "团队旧设定" in prompt1

        team_soul.write_text("团队新设定", encoding="utf-8")
        new_mtime = team_soul.stat().st_mtime + 10
        os.utime(team_soul, (new_mtime, new_mtime))

        prompt2 = await agent._get_system_prompt()
        assert "团队新设定" in prompt2
        assert "团队旧设定" not in prompt2

    @pytest.mark.asyncio
    async def test_no_rebuild_when_files_unchanged(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        test_paths.agent_soul_file("a-001").write_text("灵魂", encoding="utf-8")

        agent = _make_agent()
        with patch("tlon.agents.context_builder.SystemPromptBuilder") as mock_builder_cls:
            mock_builder_cls.return_value.build.return_value = "prompt"
            await agent._get_system_prompt()
            await agent._get_system_prompt()
            await agent._get_system_prompt()

        assert mock_builder_cls.call_count == 1


class TestKnowledgeContext:
    @pytest.mark.asyncio
    async def test_build_knowledge_returns_empty_when_no_blocks(self):
        agent = _make_agent()
        agent.memory.build_context_blocks = AsyncMock(return_value=[])
        result = await agent._build_knowledge_context()
        assert result == ""

    @pytest.mark.asyncio
    async def test_build_knowledge_formats_blocks(self):
        agent = _make_agent()
        block = MagicMock()
        block.to_openai_message.return_value = {"content": "历史记忆内容"}
        agent.memory.build_context_blocks = AsyncMock(return_value=[block])
        result = await agent._build_knowledge_context()
        assert "历史记忆内容" in result

    @pytest.mark.asyncio
    async def test_build_context_includes_knowledge(self):
        """User prompt should include knowledge as first user message."""
        from tlon.organization.messaging.message import Message

        agent = _make_agent()
        block = MagicMock()
        block.to_openai_message.return_value = {"content": "历史记忆内容"}
        agent.memory.build_context_blocks = AsyncMock(return_value=[block])
        agent.message_store = MagicMock()
        agent.message_store.get_history = AsyncMock(return_value=[])

        msg = Message.create(
            sender_id="user-001",
            sender_name="用户",
            sender_type="human",
            content="你好",
        )
        context = await agent._build_context(msg)

        # Find the knowledge message
        assert any(m["role"] == "user" and "[已知信息]" in m.get("content", "") for m in context)


class TestToolExecutionDoesNotInvalidateCache:
    """Memory-modifying tools no longer invalidate system prompt cache."""

    MEMORY_TOOLS = ["rememberFact", "forget", "generateSummary"]
    NEUTRAL_TOOLS = ["executeBash", "listTasks", "startTask", "sendMessage", "searchMemories"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", MEMORY_TOOLS + NEUTRAL_TOOLS)
    async def test_no_tool_invalidates_cache(self, tool_name):
        agent = _make_agent()
        agent._system_prompt_cache = "cached"

        mock_result = MagicMock()
        mock_result.success = True
        agent.tool_executor.execute = AsyncMock(return_value=mock_result)

        # Execute tool directly (same as what _execute_tool_calls does internally)
        await agent.tool_executor.execute(tool_name, {})

        # Cache should remain unchanged
        assert agent._system_prompt_cache == "cached"


class TestToolExecutionObservability:
    """Tool execution via _process_message (step/collect loop) publishes events."""

    @pytest.mark.asyncio
    async def test_publishes_start_and_end_events(self):
        from tlon.base.llm_client import LLMResponse, ToolCall
        from tlon.tools.base import ToolResult

        agent = _make_agent()
        agent._system_prompt_cache = "prompt"

        # LLM returns tool call + completion
        llm_client = MagicMock()
        llm_client.chat = AsyncMock(
            side_effect=[
                LLMResponse(
                    content=None,
                    tool_calls=[ToolCall(id="tc-001", function_name="sendMessage",
                                         arguments={"content": "Done!", "is_complete": True})],
                ),
            ]
        )
        agent.llm_client = llm_client

        async def _mock_execute(name, args):
            return ToolResult(
                success=True,
                data={"content": "Done!", "is_complete": True, "mentions": []},
            )

        agent.tool_executor = MagicMock()
        agent.tool_executor.get_tool_definitions = MagicMock(return_value=[])
        agent.tool_executor.execute = _mock_execute

        from tlon.organization.messaging.message import Message

        msg = Message.create(sender_id="u1", sender_name="U", sender_type="human", content="hi")
        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            await agent._process_message(msg)

        published_types = [
            call.args[0].type for call in agent.event_bus.publish.await_args_list
        ]
        from tlon.base.events.event import EventType

        assert EventType.TOOL_EXECUTE_START in published_types
        assert EventType.TOOL_EXECUTE_END in published_types

    @pytest.mark.asyncio
    async def test_publishes_error_event_on_exception(self):
        from tlon.base.llm_client import LLMResponse, ToolCall
        from tlon.tools.base import ToolResult

        agent = _make_agent()
        agent._system_prompt_cache = "prompt"

        llm_client = MagicMock()
        llm_client.chat = AsyncMock(
            side_effect=[
                LLMResponse(
                    content=None,
                    tool_calls=[ToolCall(id="tc-001", function_name="executeBash", arguments={})],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[ToolCall(id="tc-002", function_name="sendMessage",
                                         arguments={"content": "Done!", "is_complete": True})],
                ),
            ]
        )
        agent.llm_client = llm_client

        async def _mock_execute(name, args):
            if name == "executeBash":
                raise RuntimeError("boom")
            return ToolResult(
                success=True,
                data={"content": "Done!", "is_complete": True, "mentions": []},
            )

        agent.tool_executor = MagicMock()
        agent.tool_executor.get_tool_definitions = MagicMock(return_value=[])
        agent.tool_executor.execute = _mock_execute

        from tlon.organization.messaging.message import Message

        msg = Message.create(sender_id="u1", sender_name="U", sender_type="human", content="hi")
        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            await agent._process_message(msg)

        published_types = [
            call.args[0].type for call in agent.event_bus.publish.await_args_list
        ]
        from tlon.base.events.event import EventType

        assert EventType.TOOL_ERROR in published_types
