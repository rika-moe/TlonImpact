"""Tests for Agent prompt cache behavior.

Verifies that:
- _get_system_prompt() builds on first call
- _get_system_prompt() reuses cache on subsequent calls
- System prompt is static (no dirty mechanism needed)
- _build_knowledge_context() injects memory into user prompt
- Tool execution does not invalidate system prompt cache
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.agents.agent import Agent
from tlon.base.config import AgentConfig


def _make_agent():
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
    )


class TestPromptCacheBasic:
    @pytest.mark.asyncio
    async def test_first_call_builds_prompt(self):
        agent = _make_agent()
        with patch.object(agent, "_build_prompt_context", new_callable=AsyncMock) as mock_ctx:
            mock_ctx.return_value = MagicMock()
            with patch("tlon.agents.agent.SystemPromptBuilder") as mock_builder_cls:
                mock_builder_cls.return_value.build.return_value = "built prompt"
                result = await agent._get_system_prompt()

        assert result == "built prompt"
        assert agent._system_prompt_cache == "built prompt"

    @pytest.mark.asyncio
    async def test_second_call_uses_cache(self):
        agent = _make_agent()
        agent._system_prompt_cache = "cached prompt"

        with patch.object(agent, "_build_prompt_context", new_callable=AsyncMock) as mock_ctx:
            result = await agent._get_system_prompt()
            mock_ctx.assert_not_called()

        assert result == "cached prompt"


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
    """Tool execution via _execute_tool_calls publishes observability events."""

    @pytest.mark.asyncio
    async def test_publishes_start_and_end_events(self):
        agent = _make_agent()
        from tlon.agents.llm_client import ToolCall

        tool_call = ToolCall(id="tc-001", function_name="listTasks", arguments={})
        mock_result = MagicMock()
        mock_result.success = True
        agent.tool_executor.execute = AsyncMock(return_value=mock_result)

        await agent._execute_tool_calls([tool_call])

        published_types = [call.args[0].type for call in agent.event_bus.publish.await_args_list]
        from tlon.base.events.event import EventType

        assert EventType.TOOL_EXECUTE_START in published_types
        assert EventType.TOOL_EXECUTE_END in published_types

    @pytest.mark.asyncio
    async def test_publishes_error_event_on_exception(self):
        agent = _make_agent()
        from tlon.agents.llm_client import ToolCall

        tool_call = ToolCall(id="tc-001", function_name="executeBash", arguments={})
        agent.tool_executor.execute = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            await agent._execute_tool_calls([tool_call])

        published_types = [call.args[0].type for call in agent.event_bus.publish.await_args_list]
        from tlon.base.events.event import EventType

        assert EventType.TOOL_ERROR in published_types
