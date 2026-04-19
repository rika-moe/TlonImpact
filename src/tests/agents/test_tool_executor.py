"""Tests for ToolExecutor (direct execution mode).

Verifies:
- Tool registration and lookup
- Direct execute() without EventBus
- Error handling (tool not found, execution failure)
- Tool definitions for LLM
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.agents.tool_executor import ToolExecutor
from tlon.tools.base import ToolResult


class TestToolExecutorRegistration:
    def test_register_and_list_tools(self):
        executor = ToolExecutor()
        tool = MagicMock()
        tool.name = "testTool"

        executor.register_tool(tool)

        assert executor.list_tools() == ["testTool"]
        assert executor.has_tool("testTool")

    def test_register_overwrites_existing(self):
        executor = ToolExecutor()
        tool1 = MagicMock()
        tool1.name = "testTool"
        tool2 = MagicMock()
        tool2.name = "testTool"

        executor.register_tool(tool1)
        executor.register_tool(tool2)

        assert executor.get_tool("testTool") is tool2

    def test_unregister_tool(self):
        executor = ToolExecutor()
        tool = MagicMock()
        tool.name = "testTool"

        executor.register_tool(tool)
        assert executor.unregister_tool("testTool") is True
        assert not executor.has_tool("testTool")

    def test_unregister_nonexistent(self):
        executor = ToolExecutor()
        assert executor.unregister_tool("nonexistent") is False

    def test_get_tool_not_found_raises(self):
        executor = ToolExecutor()
        with pytest.raises(ValueError, match="Tool not found"):
            executor.get_tool("nonexistent")


class TestToolExecutorExecute:
    async def test_execute_tool_success(self):
        executor = ToolExecutor()
        mock_tool = MagicMock()
        mock_tool.name = "testTool"
        mock_tool.execute = AsyncMock(return_value=ToolResult(success=True, data={"result": "ok"}))

        executor.register_tool(mock_tool)
        result = await executor.execute("testTool", {"arg": "value"})

        assert result.success is True
        mock_tool.execute.assert_awaited_once_with(arg="value")

    async def test_execute_tool_not_found(self):
        executor = ToolExecutor()
        result = await executor.execute("nonexistent", {})

        assert result.success is False
        assert "not found" in result.error

    async def test_execute_tool_failure(self):
        executor = ToolExecutor()
        mock_tool = MagicMock()
        mock_tool.name = "failingTool"
        mock_tool.execute = AsyncMock(side_effect=RuntimeError("boom"))

        executor.register_tool(mock_tool)
        result = await executor.execute("failingTool", {})

        assert result.success is False
        assert "boom" in result.error


class TestToolExecutorDefinitions:
    def test_get_tool_definitions(self):
        executor = ToolExecutor()
        mock_tool = MagicMock()
        mock_tool.name = "testTool"
        mock_tool.to_openai_definition.return_value = {
            "type": "function",
            "function": {"name": "testTool"},
        }

        executor.register_tool(mock_tool)
        defs = executor.get_tool_definitions()

        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "testTool"

    def test_describe_tools(self):
        executor = ToolExecutor()
        mock_tool = MagicMock()
        mock_tool.name = "testTool"
        mock_tool.description = "A test tool"
        mock_tool.get_parameter_description.return_value = "  param1: string"

        executor.register_tool(mock_tool)
        desc = executor.describe_tools()

        assert "testTool" in desc
        assert "A test tool" in desc

    def test_describe_tools_empty(self):
        executor = ToolExecutor()
        assert executor.describe_tools() == "No tools available."


class TestToolExecutorLifecycle:
    async def test_initialize_calls_tool_initialize(self):
        executor = ToolExecutor()
        mock_tool = MagicMock()
        mock_tool.name = "testTool"
        mock_tool.initialize = AsyncMock()

        executor.register_tool(mock_tool)
        await executor.initialize()

        mock_tool.initialize.assert_awaited_once()

    async def test_shutdown_calls_tool_shutdown(self):
        executor = ToolExecutor()
        mock_tool = MagicMock()
        mock_tool.name = "testTool"
        mock_tool.shutdown = AsyncMock()

        executor.register_tool(mock_tool)
        await executor.shutdown()

        mock_tool.shutdown.assert_awaited_once()
