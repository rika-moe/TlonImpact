"""Tests for ReActStateMachine.

Verifies explicit state machine behavior:
- State transitions
- AGENT_REACT_STATE_TRANSITION event emission
- Terminal states (COMPLETED, MAX_ITERATIONS, ERROR)
- Tool execution flow via EventBus
- Error recovery
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from tlon.agents.llm_client import LLMResponse, ToolCall
from tlon.agents.react_state_machine import (
    ReActContext,
    ReActState,
    ReActStateMachine,
    StateTransition,
)
from tlon.base.events.event import Event, EventType
from tlon.tools.base import ToolResult


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


class MockEventBus:
    """Mock EventBus that can handle tool execution."""

    def __init__(self, tool_results: dict[str, ToolResult] | None = None):
        self.tool_results = tool_results or {}
        self.subscribers: dict[EventType, list] = {}
        self.published_events: list[Event] = []
        self.publish = AsyncMock(side_effect=self._handle_publish)
        self.subscribe = MagicMock(side_effect=self._handle_subscribe)
        self.unsubscribe = MagicMock()

    def _handle_subscribe(self, event_type: EventType, handler):
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        sub_id = f"sub-{len(self.subscribers[event_type])}"
        self.subscribers[event_type].append((sub_id, handler))
        return sub_id

    async def _handle_publish(self, event: Event):
        self.published_events.append(event)

        # If this is a tool execution request, auto-respond with mock result
        if event.type == EventType.TOOL_EXECUTE_REQUEST:
            from tlon.agents.tool_execution import ToolExecuteRequest, ToolExecuteResult

            payload = event.payload
            if isinstance(payload, dict):
                payload = ToolExecuteRequest(**payload)

            tool_name = payload.tool_name
            result = self.tool_results.get(
                tool_name, ToolResult(success=True, data={"result": f"mock_{tool_name}"})
            )

            # Schedule result to be published
            result_event = Event(
                type=EventType.TOOL_EXECUTE_RESULT,
                payload=ToolExecuteResult(
                    correlation_id=payload.correlation_id,
                    agent_id=payload.agent_id,
                    success=result.success,
                    result=result.to_dict()
                    if hasattr(result, "to_dict")
                    else {"data": result.data, "error": result.error},
                    error=result.error,
                ),
                source="mock_tool_executor",
            )

            # Immediately notify subscribers
            if EventType.TOOL_EXECUTE_RESULT in self.subscribers:
                for _, handler in self.subscribers[EventType.TOOL_EXECUTE_RESULT]:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(result_event)
                    else:
                        handler(result_event)

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
    event_bus=None,
    send_message_callback=None,
    save_to_memory_callback=None,
    **kwargs,
) -> ReActStateMachine:
    """Create a ReActStateMachine with mocks."""
    event_bus = event_bus or MockEventBus()
    return ReActStateMachine(
        agent_id="a-001",
        agent_name="TestAgent",
        llm_client=llm_client or MockLLMClient(),
        event_bus=event_bus,
        system_prompt_provider=lambda: "system prompt",
        tool_defs_provider=event_bus.get_tool_definitions,
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
        event_bus = MockEventBus(
            {"completeDialog": ToolResult(success=True, data={"content": "Done!", "mentions": []})}
        )
        send_msg = AsyncMock()
        save_memory = AsyncMock()

        sm = _make_state_machine(
            llm_client=llm,
            event_bus=event_bus,
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
        event_bus = MockEventBus()
        send_msg = AsyncMock()

        sm = _make_state_machine(
            llm_client=llm,
            event_bus=event_bus,
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
        event_bus = MockEventBus(
            {"completeDialog": ToolResult(success=True, data={"content": "Done!", "mentions": []})}
        )

        sm = _make_state_machine(llm_client=llm, event_bus=event_bus)
        context = _make_context()
        final_state = await sm.start(context)

        assert final_state == ReActState.COMPLETED
        assert llm.call_count == 2  # Two LLM calls


class TestReActStateTransitions:
    """Test state transition events."""

    async def test_emits_state_change_events(self):
        """Test that state changes emit AGENT_REACT_STATE_TRANSITION events."""
        event_bus = MockEventBus(
            {"completeDialog": ToolResult(success=True, data={"content": "Done!", "mentions": []})}
        )
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

        sm = _make_state_machine(
            llm_client=llm,
            event_bus=event_bus,
        )

        context = _make_context()
        await sm.start(context)

        # Check that events were published
        event_types = [e.type for e in event_bus.published_events]

        # Should have state change events
        assert EventType.AGENT_REACT_STATE_TRANSITION in event_types

    async def test_state_transition_payload(self):
        """Test state transition event payload structure."""
        event_bus = MockEventBus(
            {"completeDialog": ToolResult(success=True, data={"content": "Done!"})}
        )
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

        sm = _make_state_machine(
            llm_client=llm,
            event_bus=event_bus,
        )

        context = _make_context()
        await sm.start(context)

        # Find state change events
        state_change_events = [
            e
            for e in event_bus.published_events
            if e.type == EventType.AGENT_REACT_STATE_TRANSITION
        ]

        assert len(state_change_events) > 0

        # Check payload structure
        for event in state_change_events:
            payload = event.payload
            assert isinstance(payload, StateTransition)
            assert payload.agent_id == "a-001"
            assert payload.from_state is not None
            assert payload.to_state is not None
            assert payload.timestamp > 0

    async def test_state_transition_sequence(self):
        """Test the expected state transition sequence."""
        event_bus = MockEventBus(
            {"completeDialog": ToolResult(success=True, data={"content": "Done!"})}
        )
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

        sm = _make_state_machine(
            llm_client=llm,
            event_bus=event_bus,
        )

        context = _make_context()
        await sm.start(context)

        # Get state transitions in order
        state_change_events = [
            e
            for e in event_bus.published_events
            if e.type == EventType.AGENT_REACT_STATE_TRANSITION
        ]

        transitions = [e.payload for e in state_change_events]
        states = [(t.from_state, t.to_state) for t in transitions]

        # Expected sequence (may vary, but should include these key transitions)
        assert states[0] == ("idle", "thinking")  # First transition
        assert ("thinking", "awaiting_tool") in states
        assert ("awaiting_tool", "responding") in states
        assert ("responding", "completed") in states


class TestReActToolExecution:
    """Test tool execution flow via EventBus."""

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
        event_bus = MockEventBus(
            {"completeDialog": ToolResult(success=True, data={"content": "Done!"})}
        )

        sm = _make_state_machine(llm_client=llm, event_bus=event_bus)
        context = _make_context()
        await sm.start(context)

        # Check tool requests were published
        tool_requests = [
            e for e in event_bus.published_events if e.type == EventType.TOOL_EXECUTE_REQUEST
        ]
        assert len(tool_requests) == 3  # 2 first iteration + 1 second iteration

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
        event_bus = MockEventBus(
            {
                "testTool": ToolResult(success=True, data={"value": 42}),
                "completeDialog": ToolResult(success=True, data={"content": "Done!"}),
            }
        )

        sm = _make_state_machine(llm_client=llm, event_bus=event_bus)
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
        event_bus = MockEventBus(
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
            event_bus=event_bus,
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
        event_bus = MockEventBus(
            {"completeDialog": ToolResult(success=True, data={"content": "Done!"})}
        )

        sm = _make_state_machine(llm_client=llm, event_bus=event_bus)
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
        event_bus = MockEventBus(
            {
                "failingTool": ToolResult(success=False, data={}, error="Tool failed!"),
                "completeDialog": ToolResult(success=True, data={"content": "Done!"}),
            }
        )

        sm = _make_state_machine(llm_client=llm, event_bus=event_bus)
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
