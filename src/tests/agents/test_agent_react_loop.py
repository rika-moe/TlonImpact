"""Tests for Agent ReAct loop (direct execution mode).

Verifies:
- Successful completion with sendEmail(is_complete=true)
- Max iterations limit
- No-tool-calls retry reminder
- LLM error recovery
- Tool execution errors
- Multiple concurrent tools
- Observability events emitted
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from tlon.agents.agent import Agent, AgentState
from tlon.agents.session import AgentSession
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.config import AgentConfig
from tlon.base.events.event import EventType
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.organization.mail.mail import Mail
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


def _make_message(content: str = "Hello") -> Mail:
    return Mail.create(
        from_address="user-001@tlon.internal",
        to=["a-001@tlon.internal"],
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
    async def test_completes_with_send_email(self):
        """Agent calls sendEmail(is_complete=true) and sends final message."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="sendEmail",
                            arguments={"content": "Done!", "is_complete": True, "mentions": []},
                        )
                    ],
                )
            ]
        )

        agent = _make_agent(llm_client=llm)

        # Register a mock sendEmail tool
        mock_tool = MagicMock()
        mock_tool.name = "sendEmail"
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
        message_events = [c for c in calls if c.args[0].type == EventType.MAIL_NEW]
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
                            function_name="sendEmail",
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
        send_tool.name = "sendEmail"
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
        # LLM never calls sendEmail(is_complete=true), keeps returning other tools
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
        message_events = [c for c in calls if c.args[0].type == EventType.MAIL_NEW]
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
                            function_name="sendEmail",
                            arguments={"content": "Done!", "is_complete": True, "mentions": []},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        mock_tool = MagicMock()
        mock_tool.name = "sendEmail"
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
                            function_name="sendEmail",
                            arguments={"content": "Done!", "is_complete": True},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        mock_tool = MagicMock()
        mock_tool.name = "sendEmail"
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
        message_events = [c for c in calls if c.args[0].type == EventType.MAIL_NEW]
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
                            function_name="sendEmail",
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
        send_tool.name = "sendEmail"
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
                            function_name="sendEmail",
                            arguments={"content": "Done!", "is_complete": True},
                        )
                    ],
                )
            ]
        )

        agent = _make_agent(llm_client=llm)

        mock_tool = MagicMock()
        mock_tool.name = "sendEmail"
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
                            function_name="sendEmail",
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
        send_tool.name = "sendEmail"
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
                            function_name="sendEmail",
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
        send_tool.name = "sendEmail"
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
    async def test_send_email_does_not_end_loop(self):
        """Agent calls sendEmail(is_complete=false), sends it, then continues thinking."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="sendEmail",
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
                            function_name="sendEmail",
                            arguments={"content": "Done!", "is_complete": True, "mentions": []},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        send_tool = MagicMock()
        send_tool.name = "sendEmail"
        send_tool.execute = AsyncMock(
            side_effect=[
                ToolResult(
                    success=True,
                    data={
                        "content": "Working on it...",
                        "is_complete": False,
                        "mentions": [],
                        "action": "send_email",
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

        # Should have made 2 LLM calls (sendEmail(is_complete=false) does not end loop)
        assert llm.call_count == 2

        # Should have sent 2 messages: intermediate + final
        calls = agent.event_bus.publish.await_args_list
        message_events = [c for c in calls if c.args[0].type == EventType.MAIL_NEW]
        assert len(message_events) == 2
        assert message_events[0].args[0].payload.content == "Working on it..."
        assert message_events[1].args[0].payload.content == "Done!"

    async def test_send_mail_explicit_recipients(self):
        """sendEmail sends to the explicit `to` addresses."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="sendEmail",
                            arguments={
                                "to": ["other@tlon.internal"],
                                "content": "转发给你",
                                "is_complete": False,
                            },
                        )
                    ],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-002",
                            function_name="sendEmail",
                            arguments={"content": "Done!", "is_complete": True},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        send_tool = MagicMock()
        send_tool.name = "sendEmail"
        send_tool.execute = AsyncMock(
            side_effect=[
                ToolResult(
                    success=True,
                    data={
                        "to": ["other@tlon.internal"],
                        "content": "转发给你",
                        "is_complete": False,
                    },
                ),
                ToolResult(
                    success=True,
                    data={"content": "Done!", "is_complete": True},
                ),
            ]
        )

        agent.tool_executor.register_tool(send_tool)

        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            msg = _make_message()
            await agent._process_message(msg)

        calls = agent.event_bus.publish.await_args_list
        message_events = [c for c in calls if c.args[0].type == EventType.MAIL_NEW]
        assert message_events[0].args[0].payload.to == ["other@tlon.internal"]

    async def test_reply_headers_inherited_from_trigger_mail(self):
        """Replies default to the original sender with thread headers."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="sendEmail",
                            arguments={"content": "收到", "is_complete": True},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        send_tool = MagicMock()
        send_tool.name = "sendEmail"
        send_tool.execute = AsyncMock(
            return_value=ToolResult(
                success=True, data={"content": "收到", "is_complete": True}
            )
        )
        agent.tool_executor.register_tool(send_tool)

        with patch.object(agent, "_save_interaction_to_memory", new=AsyncMock()):
            msg = _make_message()
            await agent._process_message(msg)

        calls = agent.event_bus.publish.await_args_list
        message_events = [c for c in calls if c.args[0].type == EventType.MAIL_NEW]
        assert len(message_events) == 1
        reply = message_events[0].args[0].payload
        assert reply.to == ["user-001@tlon.internal"]
        assert reply.in_reply_to == msg.id
        assert reply.thread_id == msg.thread_id

    async def test_send_email_failure_ignored(self):
        """Failed sendEmail does not crash the loop."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="sendEmail",
                            arguments={"content": "Hey", "is_complete": False, "mentions": []},
                        )
                    ],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-002",
                            function_name="sendEmail",
                            arguments={"content": "Done!", "is_complete": True, "mentions": []},
                        )
                    ],
                ),
            ]
        )

        agent = _make_agent(llm_client=llm)

        send_tool = MagicMock()
        send_tool.name = "sendEmail"
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

        # Should complete despite failed sendEmail
        calls = agent.event_bus.publish.await_args_list
        message_events = [c for c in calls if c.args[0].type == EventType.MAIL_NEW]
        assert len(message_events) == 1
        assert message_events[0].args[0].payload.content == "Done!"


class TestReActConflictDetection:
    async def test_conflict_send_complete_with_other_tools(self):
        """sendEmail(is_complete=true) with other tools triggers conflict reminder."""
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
                            function_name="sendEmail",
                            arguments={"content": "Done!", "is_complete": True, "mentions": []},
                        ),
                    ],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-003",
                            function_name="sendEmail",
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
        send_tool.name = "sendEmail"
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
        message_events = [c for c in calls if c.args[0].type == EventType.MAIL_NEW]
        assert len(message_events) == 1
        assert message_events[0].args[0].payload.content == "Done!"


class TestAgentStateDerivation:
    """Agent-level state is derived from live sessions, not assigned per step."""

    async def test_state_derived_from_live_sessions(self):
        """One session ending must not reset state to idle while another lives."""
        agent = _make_agent()
        agent.state = AgentState.ACTIVE

        s1 = AgentSession("s1", "task A")
        s2 = AgentSession("s2", "task B")

        # Both sessions alive -> THINKING
        agent._derive_state({"s1": s1, "s2": s2})
        assert agent.state == AgentState.THINKING

        # s2 completes and is cleaned up, s1 still active -> stays THINKING
        s2.mark_completed()
        agent._derive_state({"s1": s1})
        assert agent.state == AgentState.THINKING

        # No live sessions -> ACTIVE (idle)
        agent._derive_state({})
        assert agent.state == AgentState.ACTIVE

    async def test_derive_state_leaves_lifecycle_states_untouched(self):
        """INITIALIZING/ERROR/SHUTDOWN/None are not overwritten by derivation."""
        agent = _make_agent()
        agent.state = AgentState.SHUTDOWN
        agent._derive_state({"s1": AgentSession("s1", "x")})
        assert agent.state == AgentState.SHUTDOWN

        agent.state = None
        agent._derive_state({})
        assert agent.state is None

    async def test_main_loop_state_not_reset_while_other_session_alive(self):
        """Integration: session 2 completes while session 1 still runs a tool."""
        started = asyncio.Event()
        release = asyncio.Event()

        async def hanging_tool(**kwargs):
            started.set()
            await release.wait()
            return ToolResult(success=True, data={})

        llm = MockLLMClient(
            [
                # Session 1: dispatches a hanging tool, stays WAITING
                LLMResponse(
                    content=None,
                    tool_calls=[ToolCall(id="tc-1", function_name="hangingTool", arguments={})],
                ),
                # Session 2: completes immediately
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-2",
                            function_name="sendEmail",
                            arguments={"content": "Done!", "is_complete": True},
                        )
                    ],
                ),
            ]
        )
        agent = _make_agent(llm_client=llm)
        agent._reflection.enabled = False
        agent.state = AgentState.ACTIVE

        hang = MagicMock()
        hang.name = "hangingTool"
        hang.execute = hanging_tool
        send = MagicMock()
        send.name = "sendEmail"
        send.execute = AsyncMock(
            return_value=ToolResult(
                success=True, data={"content": "Done!", "is_complete": True, "mentions": []}
            )
        )
        agent.tool_executor.register_tool(hang)
        agent.tool_executor.register_tool(send)

        agent._worker_running = True
        agent._mail_queue.put_nowait((_make_message("first"), {}))
        agent._mail_queue.put_nowait((_make_message("second"), {}))

        task = asyncio.create_task(agent._main_loop())
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            # Wait until session 2's final message has been sent
            message_events = []
            for _ in range(200):
                message_events = [
                    c
                    for c in agent.event_bus.publish.await_args_list
                    if c.args[0].type == EventType.MAIL_NEW
                ]
                if message_events:
                    break
                await asyncio.sleep(0.01)
            assert message_events
            # Let the loop run a few more passes so session 2 is cleaned up
            await asyncio.sleep(0.1)
            # Session 1 is still alive -> state must NOT fall back to ACTIVE
            assert agent.state == AgentState.THINKING
        finally:
            agent._worker_running = False
            release.set()
            await asyncio.wait_for(task, timeout=2)


class TestOrphanTaskCleanup:
    """Pending background tool tasks are cancelled when a session ends."""

    async def test_cancel_pending_cancels_unfinished_tasks(self):
        """AgentSession.cancel_pending cancels and reaps unfinished tasks."""
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def hanging():
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        session = AgentSession("s1", "x")
        task = asyncio.create_task(hanging())
        tc = ToolCall(id="tc-1", function_name="hangingTool", arguments={})
        session.dispatch_tools([tc], {"tc-1": task})
        await asyncio.wait_for(started.wait(), timeout=1)

        await session.cancel_pending()

        assert cancelled.is_set()
        assert task.cancelled()
        assert not session.has_pending_tools

    async def test_main_loop_cancels_pending_tasks_on_session_error(self):
        """Exception path: errored session's pending tool tasks are cancelled."""
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def hanging_tool(**kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return ToolResult(success=True, data={})

        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[ToolCall(id="tc-1", function_name="hangingTool", arguments={})],
                ),
            ]
        )
        agent = _make_agent(llm_client=llm)
        agent._reflection.enabled = False
        agent.state = AgentState.ACTIVE

        hang = MagicMock()
        hang.name = "hangingTool"
        hang.execute = hanging_tool
        agent.tool_executor.register_tool(hang)

        agent._worker_running = True
        agent._mail_queue.put_nowait((_make_message(), {}))

        with patch.object(
            agent, "_collect_session_results", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            task = asyncio.create_task(agent._main_loop())
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                # Session errors on collect -> cleanup must cancel the orphan
                await asyncio.wait_for(cancelled.wait(), timeout=2)
            finally:
                agent._worker_running = False
                await asyncio.wait_for(task, timeout=2)

        assert cancelled.is_set()
