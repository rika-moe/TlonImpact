"""Tests for Agent prompt cache dirty-flag behavior.

Verifies that:
- _get_system_prompt() builds on first call (dirty=True)
- _get_system_prompt() reuses cache on second call (dirty=False)
- rememberFact / forget / generateSummary invalidate the cache
- Other tools (executeBash, listTasks, etc.) do NOT invalidate the cache
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tlon.agents.agent import Agent
from tlon.agents.llm_client import ToolCall
from tlon.base.config.models import AgentConfig


def _make_agent():
    config = AgentConfig(id="a-001", name="林知遥")
    tool_executor = MagicMock()
    tool_executor.get_tool_definitions.return_value = []

    memory = MagicMock()
    memory.build_context_blocks = AsyncMock(return_value=[])

    event_bus = MagicMock()
    event_bus.publish = AsyncMock()

    return Agent(
        config=config,
        event_bus=event_bus,
        llm_client=MagicMock(),
        docker_runtime=MagicMock(),
        memory=memory,
        task_manager=MagicMock(),
        prompt_manager=MagicMock(),
        tool_executor=tool_executor,
    )


def _make_tool_call(name: str) -> ToolCall:
    tc = MagicMock(spec=ToolCall)
    tc.function_name = name
    tc.arguments = {}
    tc.id = f"call-{name}"
    return tc


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
        assert agent._system_prompt_dirty is False
        assert agent._system_prompt_cache == "built prompt"

    @pytest.mark.asyncio
    async def test_second_call_uses_cache(self):
        agent = _make_agent()
        agent._system_prompt_cache = "cached prompt"
        agent._system_prompt_dirty = False

        with patch.object(agent, "_build_prompt_context", new_callable=AsyncMock) as mock_ctx:
            result = await agent._get_system_prompt()
            mock_ctx.assert_not_called()

        assert result == "cached prompt"

    @pytest.mark.asyncio
    async def test_dirty_flag_triggers_rebuild(self):
        agent = _make_agent()
        agent._system_prompt_cache = "old prompt"
        agent._system_prompt_dirty = True

        with patch.object(agent, "_build_prompt_context", new_callable=AsyncMock) as mock_ctx:
            mock_ctx.return_value = MagicMock()
            with patch("tlon.agents.agent.SystemPromptBuilder") as mock_builder_cls:
                mock_builder_cls.return_value.build.return_value = "new prompt"
                result = await agent._get_system_prompt()

        assert result == "new prompt"
        assert agent._system_prompt_dirty is False


class TestPromptCacheInvalidation:
    """Memory-modifying tools must set _system_prompt_dirty = True."""

    MEMORY_TOOLS = ["rememberFact", "forget", "generateSummary"]
    NEUTRAL_TOOLS = ["executeBash", "listTasks", "startTask", "completeDialog", "searchMemories"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", MEMORY_TOOLS)
    async def test_memory_tool_invalidates_cache(self, tool_name):
        agent = _make_agent()
        agent._system_prompt_dirty = False
        agent._system_prompt_cache = "cached"

        tool_call = _make_tool_call(tool_name)
        mock_result = MagicMock()
        mock_result.success = True
        agent.tool_executor.execute = AsyncMock(return_value=mock_result)

        await agent._execute_tool_call(tool_call)

        assert agent._system_prompt_dirty is True, f"{tool_name} should invalidate the prompt cache"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool_name", NEUTRAL_TOOLS)
    async def test_neutral_tool_does_not_invalidate_cache(self, tool_name):
        agent = _make_agent()
        agent._system_prompt_dirty = False
        agent._system_prompt_cache = "cached"

        tool_call = _make_tool_call(tool_name)
        mock_result = MagicMock()
        mock_result.success = True
        agent.tool_executor.execute = AsyncMock(return_value=mock_result)

        await agent._execute_tool_call(tool_call)

        assert agent._system_prompt_dirty is False, (
            f"{tool_name} should NOT invalidate the prompt cache"
        )


class TestPromptCacheObservabilityEvents:
    """_execute_tool_call publishes TOOL_EXECUTE_START and TOOL_EXECUTE_END events."""

    @pytest.mark.asyncio
    async def test_publishes_start_and_end_events(self):
        agent = _make_agent()
        tool_call = _make_tool_call("listTasks")
        mock_result = MagicMock()
        mock_result.success = True
        agent.tool_executor.execute = AsyncMock(return_value=mock_result)

        await agent._execute_tool_call(tool_call)

        published_types = [call.args[0].type for call in agent.event_bus.publish.await_args_list]
        from tlon.base.events.event import EventType

        assert EventType.TOOL_EXECUTE_START in published_types
        assert EventType.TOOL_EXECUTE_END in published_types

    @pytest.mark.asyncio
    async def test_publishes_error_event_on_exception(self):
        agent = _make_agent()
        tool_call = _make_tool_call("executeBash")
        agent.tool_executor.execute = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            await agent._execute_tool_call(tool_call)

        published_types = [call.args[0].type for call in agent.event_bus.publish.await_args_list]
        from tlon.base.events.event import EventType

        assert EventType.TOOL_ERROR in published_types
