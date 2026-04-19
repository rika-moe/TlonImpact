"""Tests for messaging tools."""

import pytest

from tlon.tools.messaging.send_message import SendMessageTool


class TestSendMessageTool:
    @pytest.fixture
    def tool(self):
        return SendMessageTool()

    async def test_execute_returns_success(self, tool):
        result = await tool.execute(content="Hello team!", is_complete=True)

        assert result.success is True
        assert result.data["content"] == "Hello team!"
        assert result.data["is_complete"] is True
        assert result.data["mentions"] == []
        assert result.data["action"] == "send_message"

    async def test_execute_with_mentions(self, tool):
        result = await tool.execute(
            content="Check this out",
            is_complete=False,
            mentions=["人类", "Alice"],
        )

        assert result.success is True
        assert result.data["is_complete"] is False
        assert result.data["mentions"] == ["人类", "Alice"]

    async def test_to_openai_definition(self, tool):
        definition = tool.to_openai_definition()

        assert definition["type"] == "function"
        assert definition["function"]["name"] == "sendMessage"
        assert "content" in definition["function"]["parameters"]["properties"]
        assert "is_complete" in definition["function"]["parameters"]["properties"]
        assert "mentions" in definition["function"]["parameters"]["properties"]
        assert definition["function"]["parameters"]["required"] == ["content", "is_complete"]
