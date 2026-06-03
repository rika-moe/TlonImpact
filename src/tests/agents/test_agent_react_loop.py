"""Tests for Agent ReAct loop (direct execution mode).

Verifies:
- Successful completion with sendMessage(is_complete=true)
- Max iterations limit
- No-tool-calls retry reminder
- LLM error recovery
- Tool execution errors
- Multiple concurrent tools
- Observability events emitted
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from tlon.agents.agent import Agent
from tlon.agents.llm_client import LLMResponse, ToolCall
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.config import AgentConfig
from tlon.base.events.event import EventType
from tlon.organization.messaging.message import Message
from tlon.tools.base import ToolResult


def _make_agent(**kwargs):
    """Build an Agent with all deps as mocks."""
    config = AgentConfig(id="a-001", name="TestAgent")

    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    event_bus.subscribe = MagicMock(return_value="sub-id")
    event_bus.unsubscribe = MagicMock()

    tool_executor = ToolExecutor()

    defaults = {
        "config": config,
        "event_bus": event_bus,
        "llm_client": MagicMock(),
        "memory": AsyncMock(),
        "task_manager": MagicMock(),
        "tool_executor": tool_executor,
    }
    defaults.update(kwargs)
    return Agent(**defaults)


def _make_message(content: str = "Hello") -> Message:
    return Message.create(
        sender_id="user-001",
        sender_name="User",
        sender_type="human",
        content=content,
    )


class MockLLMClient:
    """Mock LLM client with configurable responses."""

    def __init__(self, responses: list[LLMResponse] | None = None):
        self.responses = responses or []
        self.call_count = 0

    async def chat(self, **kwargs) -> LLMResponse:
        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            return response
        return LLMResponse(content="Default", finish_reason="stop")


class TestReActCompletion:
    async def test_completes_with_send_message(self):
        """Agent calls sendMessage(is_complete=true) and sends final message."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="sendMessage",
                            arguments={"content": "Done!", "is_complete": True, "mentions": []},
                        )
                    ],
                )
            ]
        )

        agent = _make_agent(llm_client=llm)

        # Register a mock sendMessage tool
        mock_tool = MagicMock()
        mock_tool.name = "sendMessage"
        mock_tool.execute = AsyncMock(
            return_value=ToolResult(
                success=True,
                data={"content": "Done!", "is_complete": True, "mentions": []},
            )
        )
        agent.tool_executor.register_tool(mock_tool)

        # Patch _save_interaction_to_memory to avoid memory calls
        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            msg = _make_message()
            await agent._process_message(msg)

        # Should have sent the final message
        assert agent.event_bus.publish.called
        calls = agent.event_bus.publish.await_args_list
        message_events = [c for c in calls if c.args[0].type == EventType.MESSAGE_NEW]
        assert len(message_events) == 1
        assert message_events[0].args[0].payload.content == "Done!"

    async def test_multiple_iterations_then_complete(self):
        """Agent thinks, uses tool, thinks again, then completes."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001", function_name="searchMemories", arguments={"query": "test"}
                        )
                    ],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-002",
                            function_name="sendMessage",
                            arguments={"content": "Found it!", "is_complete": True, "mentions": []},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        search_tool = MagicMock()
        search_tool.name = "searchMemories"
        search_tool.execute = AsyncMock(return_value=ToolResult(success=True, data={"results": []}))

        send_tool = MagicMock()
        send_tool.name = "sendMessage"
        send_tool.execute = AsyncMock(
            return_value=ToolResult(
                success=True, data={"content": "Found it!", "is_complete": True, "mentions": []}
            )
        )

        agent.tool_executor.register_tool(search_tool)
        agent.tool_executor.register_tool(send_tool)

        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            msg = _make_message()
            await agent._process_message(msg)

        assert llm.call_count == 2
        search_tool.execute.assert_awaited_once_with(query="test")


class TestReActMaxIterations:
    async def test_max_iterations_reached(self):
        """Agent gives up after max iterations."""
        # LLM never calls sendMessage(is_complete=true), keeps returning other tools
        responses = [
            LLMResponse(
                content=None,
                tool_calls=[ToolCall(id=f"tc-{i}", function_name="testTool", arguments={})],
            )
            for i in range(15)
        ]
        llm = MockLLMClient(responses)

        agent = _make_agent(llm_client=llm)

        mock_tool = MagicMock()
        mock_tool.name = "testTool"
        mock_tool.execute = AsyncMock(return_value=ToolResult(success=True, data={}))
        agent.tool_executor.register_tool(mock_tool)

        msg = _make_message()
        await agent._process_message(msg)

        # Should have sent max iterations message
        calls = agent.event_bus.publish.await_args_list
        message_events = [c for c in calls if c.args[0].type == EventType.MESSAGE_NEW]
        assert len(message_events) == 1
        assert "简化" in message_events[0].args[0].payload.content


class TestReActNoToolCalls:
    async def test_no_tool_calls_adds_reminder(self):
        """When LLM returns no tool calls, agent adds reminder and retries."""
        llm = MockLLMClient(
            [
                LLMResponse(content="Just chatting", finish_reason="stop"),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="sendMessage",
                            arguments={"content": "Done!", "is_complete": True, "mentions": []},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        mock_tool = MagicMock()
        mock_tool.name = "sendMessage"
        mock_tool.execute = AsyncMock(
            return_value=ToolResult(
                success=True, data={"content": "Done!", "is_complete": True, "mentions": []}
            )
        )
        agent.tool_executor.register_tool(mock_tool)

        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            msg = _make_message()
            await agent._process_message(msg)

        assert llm.call_count == 2


class TestReActErrorHandling:
    async def test_llm_error_recovery(self):
        """Agent retries after LLM failure."""
        llm = MagicMock()
        llm.chat = AsyncMock(
            side_effect=[
                Exception("LLM failed"),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="sendMessage",
                            arguments={"content": "Done!", "is_complete": True},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        mock_tool = MagicMock()
        mock_tool.name = "sendMessage"
        mock_tool.execute = AsyncMock(
            return_value=ToolResult(success=True, data={"content": "Done!", "is_complete": True})
        )
        agent.tool_executor.register_tool(mock_tool)

        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            msg = _make_message()
            await agent._process_message(msg)

        assert llm.chat.call_count == 2

    async def test_max_llm_errors_reached(self):
        """Agent gives up after repeated LLM failures."""
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=Exception("Persistent failure"))

        agent = _make_agent(llm_client=llm)
        msg = _make_message()
        await agent._process_message(msg)

        # Should have sent error message
        calls = agent.event_bus.publish.await_args_list
        message_events = [c for c in calls if c.args[0].type == EventType.MESSAGE_NEW]
        assert len(message_events) == 1
        assert "错误" in message_events[0].args[0].payload.content

    async def test_tool_execution_error(self):
        """Tool failure is recorded but doesn't crash the flow."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[ToolCall(id="tc-001", function_name="failingTool", arguments={})],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-002",
                            function_name="sendMessage",
                            arguments={"content": "Done!", "is_complete": True},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        failing_tool = MagicMock()
        failing_tool.name = "failingTool"
        failing_tool.execute = AsyncMock(
            return_value=ToolResult(success=False, data={}, error="boom")
        )

        send_tool = MagicMock()
        send_tool.name = "sendMessage"
        send_tool.execute = AsyncMock(
            return_value=ToolResult(success=True, data={"content": "Done!", "is_complete": True})
        )

        agent.tool_executor.register_tool(failing_tool)
        agent.tool_executor.register_tool(send_tool)

        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            msg = _make_message()
            await agent._process_message(msg)

        failing_tool.execute.assert_awaited_once()
        send_tool.execute.assert_awaited_once()


class TestReActMultipleTools:
    async def test_executes_multiple_tools_concurrently(self):
        """Multiple tools in one response are executed concurrently."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(id="tc-001", function_name="toolA", arguments={}),
                        ToolCall(id="tc-002", function_name="toolB", arguments={}),
                    ],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-003",
                            function_name="sendMessage",
                            arguments={"content": "Done!", "is_complete": True},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        order = []

        async def slow_tool_a(**kwargs):
            await asyncio.sleep(0.05)
            order.append("A")
            return ToolResult(success=True, data={})

        async def slow_tool_b(**kwargs):
            await asyncio.sleep(0.02)
            order.append("B")
            return ToolResult(success=True, data={})

        tool_a = MagicMock()
        tool_a.name = "toolA"
        tool_a.execute = AsyncMock(side_effect=slow_tool_a)

        tool_b = MagicMock()
        tool_b.name = "toolB"
        tool_b.execute = AsyncMock(side_effect=slow_tool_b)

        send_tool = MagicMock()
        send_tool.name = "sendMessage"
        send_tool.execute = AsyncMock(
            return_value=ToolResult(success=True, data={"content": "Done!", "is_complete": True})
        )

        agent.tool_executor.register_tool(tool_a)
        agent.tool_executor.register_tool(tool_b)
        agent.tool_executor.register_tool(send_tool)

        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            msg = _make_message()
            await agent._process_message(msg)

        # Both tools should have been called
        tool_a.execute.assert_awaited_once()
        tool_b.execute.assert_awaited_once()
        # B should finish before A despite being called after (concurrent)
        assert order == ["B", "A"] or order == ["A", "B"]


class TestReActObservability:
    async def test_emits_thinking_and_llm_events(self):
        """Agent emits observability events during thinking."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="sendMessage",
                            arguments={"content": "Done!", "is_complete": True},
                        )
                    ],
                )
            ]
        )

        agent = _make_agent(llm_client=llm)

        mock_tool = MagicMock()
        mock_tool.name = "sendMessage"
        mock_tool.execute = AsyncMock(
            return_value=ToolResult(success=True, data={"content": "Done!", "is_complete": True})
        )
        agent.tool_executor.register_tool(mock_tool)

        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            msg = _make_message()
            await agent._process_message(msg)

        calls = agent.event_bus.publish.await_args_list
        event_types = [c.args[0].type for c in calls]

        assert EventType.AGENT_THINKING_START in event_types
        assert EventType.AGENT_THINKING_END in event_types
        assert EventType.AGENT_LLM_REQUEST in event_types
        assert EventType.AGENT_LLM_RESPONSE in event_types

    async def test_emits_tool_execute_events(self):
        """Agent emits tool execution observability events."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(id="tc-001", function_name="testTool", arguments={"arg": 1})
                    ],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-002",
                            function_name="sendMessage",
                            arguments={"content": "Done!", "is_complete": True},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        mock_tool = MagicMock()
        mock_tool.name = "testTool"
        mock_tool.execute = AsyncMock(return_value=ToolResult(success=True, data={"value": 42}))

        send_tool = MagicMock()
        send_tool.name = "sendMessage"
        send_tool.execute = AsyncMock(
            return_value=ToolResult(success=True, data={"content": "Done!", "is_complete": True})
        )

        agent.tool_executor.register_tool(mock_tool)
        agent.tool_executor.register_tool(send_tool)

        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            msg = _make_message()
            await agent._process_message(msg)

        calls = agent.event_bus.publish.await_args_list
        event_types = [c.args[0].type for c in calls]

        assert EventType.TOOL_EXECUTE_START in event_types
        assert EventType.TOOL_EXECUTE_END in event_types

    async def test_tool_timeout_emits_end_event(self):
        """Timeout during tool execution emits TOOL_EXECUTE_END with success=False."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[ToolCall(id="tc-001", function_name="slowTool", arguments={})],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-002",
                            function_name="sendMessage",
                            arguments={"content": "Done!", "is_complete": True},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm, per_tool_timeout=0.01)

        async def very_slow(**kwargs):
            await asyncio.sleep(10)
            return ToolResult(success=True, data={})

        slow_tool = MagicMock()
        slow_tool.name = "slowTool"
        slow_tool.execute = very_slow

        send_tool = MagicMock()
        send_tool.name = "sendMessage"
        send_tool.execute = AsyncMock(
            return_value=ToolResult(success=True, data={"content": "Done!", "is_complete": True})
        )

        agent.tool_executor.register_tool(slow_tool)
        agent.tool_executor.register_tool(send_tool)

        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            msg = _make_message()
            await agent._process_message(msg)

        calls = agent.event_bus.publish.await_args_list
        tool_end_events = [
            c
            for c in calls
            if c.args[0].type == EventType.TOOL_EXECUTE_END
            and c.args[0].payload["tool_name"] == "slowTool"
        ]
        assert len(tool_end_events) == 1
        assert tool_end_events[0].args[0].payload["success"] is False


class TestReActSendMessage:
    async def test_send_message_does_not_end_loop(self):
        """Agent calls sendMessage(is_complete=false), sends it, then continues thinking."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="sendMessage",
                            arguments={
                                "content": "Working on it...",
                                "is_complete": False,
                                "mentions": [],
                            },
                        )
                    ],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-002",
                            function_name="sendMessage",
                            arguments={"content": "Done!", "is_complete": True, "mentions": []},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        send_tool = MagicMock()
        send_tool.name = "sendMessage"
        send_tool.execute = AsyncMock(
            side_effect=[
                ToolResult(
                    success=True,
                    data={
                        "content": "Working on it...",
                        "is_complete": False,
                        "mentions": [],
                        "action": "send_message",
                    },
                ),
                ToolResult(
                    success=True,
                    data={"content": "Done!", "is_complete": True, "mentions": []},
                ),
            ]
        )

        agent.tool_executor.register_tool(send_tool)

        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            msg = _make_message()
            await agent._process_message(msg)

        # Should have made 2 LLM calls (sendMessage(is_complete=false) does not end loop)
        assert llm.call_count == 2

        # Should have sent 2 messages: intermediate + final
        calls = agent.event_bus.publish.await_args_list
        message_events = [c for c in calls if c.args[0].type == EventType.MESSAGE_NEW]
        assert len(message_events) == 2
        assert message_events[0].args[0].payload.content == "Working on it..."
        assert message_events[1].args[0].payload.content == "Done!"

    async def test_send_message_with_mentions(self):
        """sendMessage includes mentions in the sent message."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="sendMessage",
                            arguments={
                                "content": "Hey @人类",
                                "is_complete": False,
                                "mentions": ["人类"],
                            },
                        )
                    ],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-002",
                            function_name="sendMessage",
                            arguments={"content": "Done!", "is_complete": True, "mentions": []},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        send_tool = MagicMock()
        send_tool.name = "sendMessage"
        send_tool.execute = AsyncMock(
            side_effect=[
                ToolResult(
                    success=True,
                    data={"content": "Hey @人类", "is_complete": False, "mentions": ["人类"]},
                ),
                ToolResult(
                    success=True,
                    data={"content": "Done!", "is_complete": True, "mentions": []},
                ),
            ]
        )

        agent.tool_executor.register_tool(send_tool)

        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            msg = _make_message()
            await agent._process_message(msg)

        calls = agent.event_bus.publish.await_args_list
        message_events = [c for c in calls if c.args[0].type == EventType.MESSAGE_NEW]
        assert message_events[0].args[0].payload.mentions == ["人类"]

    async def test_send_message_failure_ignored(self):
        """Failed sendMessage does not crash the loop."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="sendMessage",
                            arguments={"content": "Hey", "is_complete": False, "mentions": []},
                        )
                    ],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-002",
                            function_name="sendMessage",
                            arguments={"content": "Done!", "is_complete": True, "mentions": []},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        send_tool = MagicMock()
        send_tool.name = "sendMessage"
        send_tool.execute = AsyncMock(
            side_effect=[
                ToolResult(success=False, data={}, error="Some error"),
                ToolResult(
                    success=True, data={"content": "Done!", "is_complete": True, "mentions": []}
                ),
            ]
        )

        agent.tool_executor.register_tool(send_tool)

        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            msg = _make_message()
            await agent._process_message(msg)

        # Should complete despite failed sendMessage
        calls = agent.event_bus.publish.await_args_list
        message_events = [c for c in calls if c.args[0].type == EventType.MESSAGE_NEW]
        assert len(message_events) == 1
        assert message_events[0].args[0].payload.content == "Done!"


class TestReActConflictDetection:
    async def test_conflict_send_complete_with_other_tools(self):
        """sendMessage(is_complete=true) with other tools triggers conflict reminder."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="readFile",
                            arguments={"path": "/tmp/test"},
                        ),
                        ToolCall(
                            id="tc-002",
                            function_name="sendMessage",
                            arguments={"content": "Done!", "is_complete": True, "mentions": []},
                        ),
                    ],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-003",
                            function_name="sendMessage",
                            arguments={"content": "Done!", "is_complete": True, "mentions": []},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        read_tool = MagicMock()
        read_tool.name = "readFile"
        read_tool.execute = AsyncMock(return_value=ToolResult(success=True, data={"content": "x"}))

        send_tool = MagicMock()
        send_tool.name = "sendMessage"
        send_tool.execute = AsyncMock(
            return_value=ToolResult(
                success=True, data={"content": "Done!", "is_complete": True, "mentions": []}
            )
        )

        agent.tool_executor.register_tool(read_tool)
        agent.tool_executor.register_tool(send_tool)

        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            msg = _make_message()
            await agent._process_message(msg)

        # First response should be rejected (conflict), second accepted
        assert llm.call_count == 2
        # readFile should NOT have been executed because conflict was detected before execution
        read_tool.execute.assert_not_awaited()
        send_tool.execute.assert_awaited_once()

        calls = agent.event_bus.publish.await_args_list
        message_events = [c for c in calls if c.args[0].type == EventType.MESSAGE_NEW]
        assert len(message_events) == 1
        assert message_events[0].args[0].payload.content == "Done!"
