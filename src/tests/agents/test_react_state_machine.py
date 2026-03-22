"""Tests for ReActStateMachine.

Verifies explicit state machine behavior:
- State transitions
- AGENT_REACT_STATE_TRANSITION event emission
- Terminal states (COMPLETED, MAX_ITERATIONS, ERROR)
- Tool execution flow
- Error recovery
"""

from unittest.mock import AsyncMock, MagicMock

from tlon.agents.llm_client import LLMResponse, ToolCall
from tlon.agents.react_state_machine import (
    ReActContext,
    ReActState,
    ReActStateMachine,
    StateTransition,
)
from tlon.base.events.event import EventType
from tlon.subsystem.tools.base import ToolResult


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
        # Default response - no tool calls
        return LLMResponse(content="Default response", finish_reason="stop")


class MockToolExecutor:
    """Mock tool executor."""

    def __init__(self, results: dict[str, ToolResult] | None = None):
        self.results = results or {}
        self.call_count = 0
        self.executed_tools: list[tuple[str, dict]] = []

    async def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        self.call_count += 1
        self.executed_tools.append((tool_name, arguments))
        return self.results.get(
            tool_name, ToolResult(success=True, data={"result": f"mock_{tool_name}"})
        )

    def get_tool_definitions(self) -> list[dict]:
        return [
            {"function": {"name": "completeDialog", "description": "Complete dialog"}},
            {"function": {"name": "testTool", "description": "Test tool"}},
        ]


def _make_context(messages: list | None = None, **kwargs) -> ReActContext:
    """Create a ReActContext with defaults."""
    defaults = {
        "messages": messages or [{"role": "user", "content": "Hello"}],
        "original_message_id": "msg-001",
        "original_message_sender_id": "user-001",
        "original_message_sender_type": "human",
        "max_iterations": 10,
    }
    defaults.update(kwargs)
    return ReActContext(**defaults)


def _make_state_machine(
    llm_client=None,
    tool_executor=None,
    event_bus=None,
    send_message_callback=None,
    save_to_memory_callback=None,
    **kwargs,
) -> ReActStateMachine:
    """Create a ReActStateMachine with mocks."""
    return ReActStateMachine(
        agent_id="a-001",
        agent_name="TestAgent",
        llm_client=llm_client or MockLLMClient(),
        tool_executor=tool_executor or MockToolExecutor(),
        event_bus=event_bus or AsyncMock(),
        system_prompt_provider=lambda: "system prompt",
        tool_defs_provider=lambda: [],
        send_message_callback=send_message_callback or AsyncMock(),
        save_to_memory_callback=save_to_memory_callback or AsyncMock(),
        **kwargs,
    )


class TestReActStateMachineBasics:
    """Test basic state machine functionality."""

    async def test_starts_in_idle_state(self):
        sm = _make_state_machine()
        assert sm.state == ReActState.IDLE

    async def test_completes_with_complete_dialog(self):
        """Test successful completion with completeDialog tool."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="completeDialog",
                            arguments={"content": "Done!", "mentions": []},
                        )
                    ],
                )
            ]
        )
        tools = MockToolExecutor(
            {"completeDialog": ToolResult(success=True, data={"content": "Done!", "mentions": []})}
        )
        send_msg = AsyncMock()
        save_memory = AsyncMock()

        sm = _make_state_machine(
            llm_client=llm,
            tool_executor=tools,
            send_message_callback=send_msg,
            save_to_memory_callback=save_memory,
        )

        context = _make_context()
        final_state = await sm.start(context)

        assert final_state == ReActState.COMPLETED
        assert sm.state == ReActState.COMPLETED
        send_msg.assert_called_once_with("Done!", [])
        save_memory.assert_called_once()

    async def test_max_iterations_reached(self):
        """Test max iterations limit."""
        # LLM never calls completeDialog, just keeps returning other tools
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[ToolCall(id=f"tc-{i}", function_name="testTool", arguments={})],
                    finish_reason="tool_calls",
                )
                for i in range(15)  # More than max_iterations
            ]
        )
        tools = MockToolExecutor()
        send_msg = AsyncMock()

        sm = _make_state_machine(
            llm_client=llm,
            tool_executor=tools,
            send_message_callback=send_msg,
        )

        context = _make_context(max_iterations=3)
        final_state = await sm.start(context)

        assert final_state == ReActState.MAX_ITERATIONS
        assert sm.state == ReActState.MAX_ITERATIONS
        # Should have sent max iterations message
        send_msg.assert_called_once()

    async def test_no_tool_calls_triggers_retry(self):
        """Test that no tool calls leads to a retry prompt."""
        llm = MockLLMClient(
            [
                LLMResponse(content="Just chatting", finish_reason="stop"),  # No tool calls
                LLMResponse(  # Second call with completeDialog
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="completeDialog",
                            arguments={"content": "Done!", "mentions": []},
                        )
                    ],
                    finish_reason="tool_calls",
                ),
            ]
        )
        tools = MockToolExecutor(
            {"completeDialog": ToolResult(success=True, data={"content": "Done!", "mentions": []})}
        )

        sm = _make_state_machine(llm_client=llm, tool_executor=tools)
        context = _make_context()
        final_state = await sm.start(context)

        assert final_state == ReActState.COMPLETED
        assert llm.call_count == 2  # Two LLM calls


class TestReActStateTransitions:
    """Test state transition events."""

    async def test_emits_state_change_events(self):
        """Test that state changes emit AGENT_REACT_STATE_TRANSITION events."""
        event_bus = AsyncMock()
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="tc-001",
                            function_name="completeDialog",
                            arguments={"content": "Done!", "mentions": []},
                        )
                    ],
                )
            ]
        )
        tools = MockToolExecutor(
            {"completeDialog": ToolResult(success=True, data={"content": "Done!", "mentions": []})}
        )

        sm = _make_state_machine(
            llm_client=llm,
            tool_executor=tools,
            event_bus=event_bus,
        )

        context = _make_context()
        await sm.start(context)

        # Check that events were published
        publish_calls = event_bus.publish.call_args_list
        event_types = [c.args[0].type for c in publish_calls]

        # Should have state change events
        assert EventType.AGENT_REACT_STATE_TRANSITION in event_types

    async def test_state_transition_payload(self):
        """Test state transition event payload structure."""
        event_bus = AsyncMock()
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(id="tc-001", function_name="completeDialog", arguments={})
                    ],
                )
            ]
        )
        tools = MockToolExecutor(
            {"completeDialog": ToolResult(success=True, data={"content": "Done!"})}
        )

        sm = _make_state_machine(
            llm_client=llm,
            tool_executor=tools,
            event_bus=event_bus,
        )

        context = _make_context()
        await sm.start(context)

        # Find state change events
        state_change_calls = [
            c
            for c in event_bus.publish.call_args_list
            if c.args[0].type == EventType.AGENT_REACT_STATE_TRANSITION
        ]

        assert len(state_change_calls) > 0

        # Check payload structure
        for sc in state_change_calls:
            event = sc.args[0]
            payload = event.payload
            assert isinstance(payload, StateTransition)
            assert payload.agent_id == "a-001"
            assert payload.from_state is not None
            assert payload.to_state is not None
            assert payload.timestamp > 0

    async def test_state_transition_sequence(self):
        """Test the expected state transition sequence."""
        event_bus = AsyncMock()
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(id="tc-001", function_name="completeDialog", arguments={})
                    ],
                )
            ]
        )
        tools = MockToolExecutor(
            {"completeDialog": ToolResult(success=True, data={"content": "Done!"})}
        )

        sm = _make_state_machine(
            llm_client=llm,
            tool_executor=tools,
            event_bus=event_bus,
        )

        context = _make_context()
        await sm.start(context)

        # Get state transitions in order
        state_change_calls = [
            c
            for c in event_bus.publish.call_args_list
            if c.args[0].type == EventType.AGENT_REACT_STATE_TRANSITION
        ]

        transitions = [c.args[0].payload for c in state_change_calls]
        states = [(t.from_state, t.to_state) for t in transitions]

        # Expected sequence (may vary, but should include these key transitions)
        assert states[0] == ("idle", "thinking")  # First transition
        assert ("thinking", "awaiting_tool") in states
        assert ("awaiting_tool", "responding") in states
        assert ("responding", "completed") in states


class TestReActToolExecution:
    """Test tool execution flow."""

    async def test_executes_multiple_tools(self):
        """Test executing multiple tools in one iteration."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(id="tc-001", function_name="testTool", arguments={"arg1": 1}),
                        ToolCall(id="tc-002", function_name="testTool", arguments={"arg2": 2}),
                    ],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(id="tc-003", function_name="completeDialog", arguments={})
                    ],
                ),
            ]
        )
        tools = MockToolExecutor(
            {"completeDialog": ToolResult(success=True, data={"content": "Done!"})}
        )

        sm = _make_state_machine(llm_client=llm, tool_executor=tools)
        context = _make_context()
        await sm.start(context)

        # Should have executed both tools
        assert len(tools.executed_tools) == 3  # 2 first iteration + 1 second iteration

    async def test_tool_results_added_to_context(self):
        """Test that tool results are added to the context messages."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[ToolCall(id="tc-001", function_name="testTool", arguments={})],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(id="tc-002", function_name="completeDialog", arguments={})
                    ],
                ),
            ]
        )
        tools = MockToolExecutor(
            {
                "testTool": ToolResult(success=True, data={"value": 42}),
                "completeDialog": ToolResult(success=True, data={"content": "Done!"}),
            }
        )

        sm = _make_state_machine(llm_client=llm, tool_executor=tools)
        context = _make_context()
        await sm.start(context)

        # Check context has tool messages
        tool_messages = [m for m in context.messages if m.get("role") == "tool"]
        assert len(tool_messages) >= 1

    async def test_complete_dialog_extraction(self):
        """Test that completeDialog response is properly extracted."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(id="tc-001", function_name="testTool", arguments={}),
                        ToolCall(
                            id="tc-002",
                            function_name="completeDialog",
                            arguments={"content": "Final answer!", "mentions": ["user1"]},
                        ),
                    ],
                )
            ]
        )
        tools = MockToolExecutor(
            {
                "testTool": ToolResult(success=True, data={}),
                "completeDialog": ToolResult(
                    success=True, data={"content": "Final answer!", "mentions": ["user1"]}
                ),
            }
        )
        send_msg = AsyncMock()

        sm = _make_state_machine(
            llm_client=llm,
            tool_executor=tools,
            send_message_callback=send_msg,
        )

        context = _make_context()
        await sm.start(context)

        # Verify the final message was sent with correct content and mentions
        send_msg.assert_called_once_with("Final answer!", ["user1"])


class TestReActErrorHandling:
    """Test error handling in state machine."""

    async def test_llm_error_recovery(self):
        """Test recovery from LLM errors."""
        llm = MagicMock()
        llm.chat = AsyncMock(
            side_effect=[
                Exception("LLM failed"),  # First call fails
                LLMResponse(  # Second call succeeds
                    content=None,
                    tool_calls=[
                        ToolCall(id="tc-001", function_name="completeDialog", arguments={})
                    ],
                ),
            ]
        )
        tools = MockToolExecutor(
            {"completeDialog": ToolResult(success=True, data={"content": "Done!"})}
        )

        sm = _make_state_machine(llm_client=llm, tool_executor=tools)
        context = _make_context(max_errors=3)
        final_state = await sm.start(context)

        assert final_state == ReActState.COMPLETED
        assert llm.chat.call_count == 2

    async def test_max_errors_reached(self):
        """Test that max errors leads to ERROR state."""
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=Exception("Persistent failure"))
        send_msg = AsyncMock()

        sm = _make_state_machine(
            llm_client=llm,
            send_message_callback=send_msg,
        )

        context = _make_context(max_errors=2)
        final_state = await sm.start(context)

        assert final_state == ReActState.ERROR
        assert context.error_count >= 2
        # Should send error message
        send_msg.assert_called_once()

    async def test_tool_execution_error(self):
        """Test handling of tool execution errors."""
        llm = MockLLMClient(
            [
                LLMResponse(
                    content=None,
                    tool_calls=[ToolCall(id="tc-001", function_name="failingTool", arguments={})],
                ),
                LLMResponse(
                    content=None,
                    tool_calls=[
                        ToolCall(id="tc-002", function_name="completeDialog", arguments={})
                    ],
                ),
            ]
        )
        tools = MockToolExecutor(
            {
                "failingTool": ToolResult(success=False, data={}, error="Tool failed!"),
                "completeDialog": ToolResult(success=True, data={"content": "Done!"}),
            }
        )

        sm = _make_state_machine(llm_client=llm, tool_executor=tools)
        context = _make_context()
        final_state = await sm.start(context)

        assert final_state == ReActState.COMPLETED
        # Tool failure should be recorded but not crash the flow


class TestReActContext:
    """Test ReActContext data structure."""

    def test_context_defaults(self):
        ctx = ReActContext(
            messages=[{"role": "user", "content": "Hello"}],
            original_message_id="msg-001",
            original_message_sender_id="user-001",
            original_message_sender_type="human",
        )
        assert ctx.iteration == 0
        assert ctx.max_iterations == 10
        assert ctx.error_count == 0
        assert ctx.max_errors == 3
        assert ctx.all_tool_results == []

    def test_context_with_custom_values(self):
        ctx = ReActContext(
            messages=[],
            original_message_id="msg-002",
            original_message_sender_id="user-002",
            original_message_sender_type="agent",
            max_iterations=5,
            max_errors=2,
        )
        assert ctx.max_iterations == 5
        assert ctx.max_errors == 2

    def test_context_is_mutable(self):
        ctx = ReActContext(
            messages=[],
            original_message_id="msg-001",
            original_message_sender_id="user-001",
            original_message_sender_type="human",
        )
        ctx.iteration += 1
        ctx.messages.append({"role": "assistant", "content": "Hi"})
        ctx.all_tool_results.append({"tool": "test"})

        assert ctx.iteration == 1
        assert len(ctx.messages) == 1
        assert len(ctx.all_tool_results) == 1
