"""ReAct State Machine for Agent message processing.

Explicit state machine replacing the implicit for-loop in _process_message().
Makes the ReAct flow observable, testable, and recoverable.

Tool execution is via EventBus for async operation.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from tlon.agents.llm_client import LLMClient, LLMResponse, ToolCall
from tlon.agents.tool_execution import (
    ToolExecuteRequest,
    ToolExecutionManager,
    ToolTimeoutError,
)
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.subsystem.tools.base import ToolResult

# Re-export for convenience
AGENT_REACT_STATE_TRANSITION = EventType.AGENT_REACT_STATE_TRANSITION

logger = get_logger(__name__)


class ReActState(Enum):
    """States in the ReAct loop state machine.

    IDLE:         Waiting for a message to process
    THINKING:     LLM inference in progress
    AWAITING_TOOL: Waiting for tool execution (semantic placeholder for Phase 3)
    RESPONDING:   Preparing to send final response
    ERROR:        Error recovery state
    COMPLETED:    Processing completed successfully
    MAX_ITERATIONS: Max iterations reached, giving up
    """

    IDLE = "idle"
    THINKING = "thinking"
    AWAITING_TOOL = "awaiting_tool"
    RESPONDING = "responding"
    ERROR = "error"
    COMPLETED = "completed"
    MAX_ITERATIONS = "max_iterations"


@dataclass
class ReActContext:
    """Runtime context for the ReAct state machine.

    Serializable for potential persistence and recovery.

    Attributes:
        messages: Conversation history (OpenAI format)
        iteration: Current iteration count
        max_iterations: Maximum allowed iterations
        original_message_id: ID of the message being processed
        original_message_sender_id: ID of the sender
        original_message_sender_type: "human" or "agent"
        all_tool_results: Accumulated tool results for memory
        final_response: Extracted response content (when completeDialog called)
        final_mentions: Extracted mentions (when completeDialog called)
        error_count: Number of consecutive errors
        max_errors: Maximum allowed errors before giving up
    """

    messages: list[dict[str, Any]]
    original_message_id: str
    original_message_sender_id: str
    original_message_sender_type: str
    iteration: int = 0
    max_iterations: int = 10
    all_tool_results: list[dict] = field(default_factory=list)
    final_response: str | None = None
    final_mentions: list[str] = field(default_factory=list)
    error_count: int = 0
    max_errors: int = 3
    trace_id: str | None = None
    span_id: str | None = None


@dataclass
class StateTransition:
    """Represents a state transition event payload.

    Attributes:
        agent_id: ID of the agent
        from_state: Previous state
        to_state: New state
        iteration: Current iteration
        timestamp: Unix timestamp
        reason: Optional reason for transition
    """

    agent_id: str
    from_state: str
    to_state: str
    iteration: int
    timestamp: float
    reason: str | None = None


class ReActStateMachine:
    """Explicit state machine for ReAct message processing.

    Replaces the implicit for-loop in Agent._process_message() with
    explicit state transitions and observability.

    Tool execution is via EventBus for async operation.

    Example:
        >>> context = ReActContext(
        ...     messages=[...],
        ...     original_message_id="msg-001",
        ...     original_message_sender_id="user-001",
        ...     original_message_sender_type="human",
        ... )
        >>> sm = ReActStateMachine(agent_id="a-001", llm_client=llm, event_bus=bus)
        >>> await sm.start(context)
        >>> # State machine drives itself through states until COMPLETED/ERROR
    """

    def __init__(
        self,
        agent_id: str,
        agent_name: str,
        llm_client: LLMClient,
        event_bus: EventBus,
        system_prompt_provider: Callable[[], str] | Callable[[], Awaitable[str]],
        tool_defs_provider: Callable[[], list[dict]],
        send_message_callback: Callable[[str, list[str]], Any],
        save_to_memory_callback: Callable[[Any, Any, list], Any],
        total_tool_timeout: float = 60.0,
        per_tool_timeout: float = 30.0,
    ):
        """Initialize the ReAct state machine.

        Args:
            agent_id: Unique identifier for the agent
            agent_name: Human-readable name
            llm_client: LLM client for inference
            event_bus: Event bus for publishing state changes and tool execution
            system_prompt_provider: Callback to get system prompt
            tool_defs_provider: Callback to get tool definitions
            send_message_callback: Callback to send final message
            save_to_memory_callback: Callback to save interaction to memory
            total_tool_timeout: Total timeout for all tools (deadline mode)
            per_tool_timeout: Timeout for each individual tool
        """
        self.agent_id = agent_id
        self.agent_name = agent_name
        self._llm_client = llm_client
        self._event_bus = event_bus
        self._get_system_prompt = system_prompt_provider
        self._get_tool_definitions = tool_defs_provider
        self._send_message = send_message_callback
        self._save_to_memory = save_to_memory_callback
        self._total_tool_timeout = total_tool_timeout
        self._per_tool_timeout = per_tool_timeout

        self.state = ReActState.IDLE
        self._context: ReActContext | None = None
        self._last_llm_response: LLMResponse | None = None

        # Tool execution over EventBus
        self._tool_execution_manager = ToolExecutionManager(default_timeout=per_tool_timeout)
        self._tool_result_subscription: str | None = None

    async def start(self, context: ReActContext) -> ReActState:
        """Start processing a message.

        Drives the state machine by repeatedly calling state handlers
        until a terminal state is reached. Each handler returns the
        next state, making control flow explicit and easy to follow.

        Args:
            context: Initial ReAct context

        Returns:
            Final state (COMPLETED, MAX_ITERATIONS, or ERROR)
        """
        self._context = context

        # Subscribe to tool results
        self._tool_result_subscription = self._event_bus.subscribe(
            EventType.TOOL_EXECUTE_RESULT, self._on_tool_result
        )

        try:
            # Initial transition to THINKING
            await self._transition(ReActState.THINKING, reason="Message received")

            # Run state machine: each handler returns the next state
            # Terminal states (ERROR, MAX_ITERATIONS, COMPLETED) get one final handler call
            # to perform cleanup/send messages before exiting
            while True:
                # Check if we've reached a terminal state (after the first iteration)
                if self.state in (
                    ReActState.COMPLETED,
                    ReActState.MAX_ITERATIONS,
                    ReActState.ERROR,
                    ReActState.IDLE,
                ):
                    # One final call to the handler for cleanup
                    await self._on_enter(self.state)
                    break

                next_state = await self._on_enter(self.state)
                if next_state is None:
                    break
                if next_state != self.state:
                    # State transition
                    await self._transition(next_state)
                # If next_state == self.state (e.g., THINKING -> THINKING for retry),
                # continue the loop without emitting transition event

            return self.state
        finally:
            # Cleanup: unsubscribe from tool results
            if self._tool_result_subscription:
                self._event_bus.unsubscribe(self._tool_result_subscription)
                self._tool_result_subscription = None

    async def _transition(self, new_state: ReActState, reason: str | None = None) -> None:
        """Transition to a new state and emit event.

        Args:
            new_state: The state to transition to
            reason: Optional reason for the transition
        """
        old_state = self.state
        self.state = new_state

        # Emit state change event
        await self._emit_state_change(old_state, new_state, reason)

    async def _emit_state_change(
        self, old_state: ReActState, new_state: ReActState, reason: str | None = None
    ) -> None:
        """Publish state change event to EventBus.

        Args:
            old_state: Previous state
            new_state: New state
            reason: Optional reason for transition
        """
        if self._context is None:
            return

        transition = StateTransition(
            agent_id=self.agent_id,
            from_state=old_state.value,
            to_state=new_state.value,
            iteration=self._context.iteration,
            timestamp=time.time(),
            reason=reason,
        )

        event = Event(
            type=EventType.AGENT_REACT_STATE_TRANSITION,
            payload=transition,
            source=self.agent_id,
            trace_id=self._context.trace_id,
            span_id=self._context.span_id,
        )
        await self._event_bus.publish(event)

        logger.debug(
            f"[{self.agent_name}] State: {old_state.value} -> {new_state.value} "
            f"(iteration {self._context.iteration})"
        )

    async def _on_enter(self, state: ReActState) -> ReActState | None:
        """Handle entering a state.

        Each handler executes the state's logic and returns the next state.
        This makes the control flow explicit and easy to follow.

        Args:
            state: The state being entered

        Returns:
            The next state to transition to, or None to stay in current state
        """
        match state:
            case ReActState.THINKING:
                return await self._do_thinking()
            case ReActState.AWAITING_TOOL:
                return await self._do_awaiting_tool()
            case ReActState.RESPONDING:
                return await self._do_responding()
            case ReActState.ERROR:
                return await self._do_error_recovery()
            case ReActState.COMPLETED:
                return await self._do_completed()
            case ReActState.MAX_ITERATIONS:
                return await self._do_max_iterations()
            case ReActState.IDLE:
                # Terminal state - do nothing
                return None

    async def _do_thinking(self) -> ReActState:
        """Execute THINKING state: call LLM and decide next state.

        Returns:
            Next state (AWAITING_TOOL, THINKING for retry, or ERROR)
        """
        if self._context is None:
            return ReActState.ERROR

        # Check iteration limit
        if self._context.iteration >= self._context.max_iterations:
            return ReActState.MAX_ITERATIONS

        self._context.iteration += 1

        try:
            # Call LLM
            # Handle both sync and async system_prompt_provider
            import inspect

            system_prompt_result = self._get_system_prompt()
            if inspect.iscoroutine(system_prompt_result):
                system_prompt = await system_prompt_result
            else:
                system_prompt = system_prompt_result
            tools = self._get_tool_definitions()

            response = await self._llm_client.chat(
                system_prompt=system_prompt,
                messages=self._context.messages,
                tools=tools if tools else None,
            )
            self._last_llm_response = response

            logger.debug(
                f"LLM response: finish_reason={response.finish_reason}, "
                f"tool_calls={len(response.tool_calls) if response.tool_calls else 0}"
            )

            # Decide next state based on response
            if response.tool_calls:
                return ReActState.AWAITING_TOOL
            else:
                # No tool calls - need to prompt for completeDialog
                await self._add_no_tool_reminder(response)
                # Stay in THINKING state for next iteration
                return ReActState.THINKING

        except Exception as e:
            logger.error(f"LLM call failed: {e}", exc_info=True)
            self._context.error_count += 1
            if self._context.error_count >= self._context.max_errors:
                return ReActState.ERROR
            else:
                # Retry thinking
                return ReActState.THINKING

    async def _do_awaiting_tool(self) -> ReActState:
        """Execute AWAITING_TOOL state: execute tools and decide next state.

        Tool execution is via EventBus.

        Returns:
            Next state (RESPONDING if completeDialog called, THINKING to continue, or ERROR)
        """
        if self._context is None or self._last_llm_response is None:
            return ReActState.ERROR

        response = self._last_llm_response
        tool_calls = response.tool_calls

        if not tool_calls:
            return ReActState.THINKING

        # Log tool calls
        tool_names = [tc.function_name for tc in tool_calls]
        logger.info(f"{self.agent_name} using {len(tool_calls)} tool(s): {', '.join(tool_names)}")

        # Execute tools via EventBus
        tool_results_list = await self._execute_tools(tool_calls)

        if tool_results_list is None:
            # Error during execution
            return ReActState.ERROR

        # Check for completeDialog
        complete_dialog_called = any(
            tc.function_name == "completeDialog" for tc, _ in tool_results_list
        )
        final_response: str | None = None
        final_mentions: list[str] = []

        for tc, result in tool_results_list:
            if tc.function_name == "completeDialog" and result.success:
                final_response = result.data.get("content", "")
                final_mentions = result.data.get("mentions", [])

        # Add assistant message with tool calls
        assistant_msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [self._tool_call_to_dict(tc) for tc, _ in tool_results_list],
        }
        if response.reasoning_content:
            assistant_msg["reasoning_content"] = response.reasoning_content
        self._context.messages.append(assistant_msg)

        # Add tool results to context
        for tool_call, tool_result in tool_results_list:
            self._context.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": self._tool_result_to_string(tool_result),
                }
            )

        # Decide next state
        if complete_dialog_called and final_response is not None:
            self._context.final_response = final_response
            self._context.final_mentions = final_mentions
            return ReActState.RESPONDING
        else:
            # Continue thinking
            return ReActState.THINKING

    async def _execute_tools(
        self, tool_calls: list[ToolCall]
    ) -> list[tuple[ToolCall, ToolResult]] | None:
        """Execute tools via EventBus.

        Publishes TOOL_EXECUTE_REQUEST events and waits for TOOL_EXECUTE_RESULT.
        Supports concurrent execution and timeout handling.

        Args:
            tool_calls: List of tool calls to execute

        Returns:
            List of (tool_call, result) tuples, or None on error
        """
        import uuid

        # Create futures for all tool calls
        futures: dict[str, asyncio.Future[ToolResult]] = {}

        for tool_call in tool_calls:
            correlation_id = str(uuid.uuid4())
            future = self._tool_execution_manager.create_future(correlation_id)
            futures[tool_call.id] = (tool_call, correlation_id, future)

            # Log with truncated args
            args_str = str(tool_call.arguments) if hasattr(tool_call, "arguments") else "{}"
            if len(args_str) > 100:
                args_str = args_str[:100] + "..."
            logger.info(
                f"  → Requesting: {tool_call.function_name}({args_str}) [id={correlation_id[:8]}]"
            )

            # Publish tool execution request
            request = ToolExecuteRequest(
                correlation_id=correlation_id,
                agent_id=self.agent_id,
                tool_name=tool_call.function_name,
                arguments=tool_call.arguments,
                timeout_seconds=self._per_tool_timeout,
            )

            await self._event_bus.publish(
                Event(
                    type=EventType.TOOL_EXECUTE_REQUEST,
                    payload=request,
                    source=self.agent_id,
                )
            )

        # Wait for all results with hybrid timeout strategy:
        # - Total timeout (deadline) prevents runaway execution
        # - Per-tool timeout protects against individual slow tools
        try:
            deadline = asyncio.get_event_loop().time() + self._total_tool_timeout
            tool_results_list: list[tuple[ToolCall, ToolResult]] = []

            for tool_call, correlation_id, future in futures.values():
                # Calculate remaining time until total deadline
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    # Already past total deadline
                    logger.error(
                        f"Total tool timeout exceeded after {self._total_tool_timeout}s "
                        f"({len(tool_results_list)}/{len(futures)} tools completed)"
                    )
                    raise TimeoutError()

                # Use the smaller of: remaining time or per-tool timeout
                timeout = min(remaining, self._per_tool_timeout)

                try:
                    result = await asyncio.wait_for(future, timeout=timeout)
                    tool_results_list.append((tool_call, result))
                    self._context.all_tool_results.append(
                        {
                            "tool_call": tool_call,
                            "result": result,
                        }
                    )
                    success = result.success
                    msg = (
                        result.message
                        if hasattr(result, "message") and result.message
                        else ("OK" if success else "Failed")
                    )
                    logger.info(f"  ✓ Result: {msg} [id={correlation_id[:8]}]")
                except TimeoutError:
                    self._tool_execution_manager.cleanup(correlation_id)
                    logger.error(
                        f"Tool timeout: {tool_call.function_name} [id={correlation_id[:8]}] "
                        f"(timeout={timeout:.1f}s)"
                    )
                    raise ToolTimeoutError([correlation_id], timeout) from None

            return tool_results_list

        except ToolTimeoutError as e:
            logger.error(f"Tools timed out: {e.correlation_ids}")
            # Mark all pending as stale and return error results
            for tool_call, correlation_id, _ in futures.values():
                if correlation_id in self._tool_execution_manager.get_pending_ids():
                    self._tool_execution_manager.cleanup(correlation_id)
                    self._tool_execution_manager.mark_stale(correlation_id)
                    result = ToolResult(
                        success=False,
                        error=f"Timeout (total={self._total_tool_timeout}s, per-tool={self._per_tool_timeout}s)",
                    )
                    tool_results_list.append((tool_call, result))
                    self._context.all_tool_results.append(
                        {
                            "tool_call": tool_call,
                            "result": result,
                        }
                    )
            return tool_results_list if tool_results_list else None

        except Exception as e:
            logger.error(f"Tool execution via EventBus failed: {e}", exc_info=True)
            return None

    async def _on_tool_result(self, event: Event) -> None:
        """Handle tool execution result from EventBus.

        Args:
            event: TOOL_EXECUTE_RESULT event
        """
        from tlon.agents.tool_execution import ToolExecuteResult

        try:
            # Handle both dict payload and dataclass payload
            payload = event.payload
            if isinstance(payload, ToolExecuteResult):
                result_data = payload
            elif isinstance(payload, dict):
                result_data = ToolExecuteResult(**payload)
            else:
                logger.error(f"Unexpected payload type: {type(payload)}")
                return
        except (TypeError, KeyError) as e:
            logger.error(f"Invalid tool result payload: {e}")
            return

        # Check if this is for us (filter out results for other agents in multi-agent setup)
        if result_data.agent_id != self.agent_id:
            logger.debug(
                f"Ignoring tool result for different agent: {result_data.agent_id} "
                f"(we are {self.agent_id})"
            )
            return

        correlation_id = result_data.correlation_id

        # Check for stale results (already timed out)
        if self._tool_execution_manager.is_stale_result(correlation_id):
            logger.debug(f"Ignoring stale tool result: {correlation_id[:8]}")
            return

        # Resolve the future
        if result_data.success and result_data.result:
            result = ToolResult(**result_data.result)
        else:
            result = ToolResult(success=False, data={}, error=result_data.error or "Unknown error")

        resolved = self._tool_execution_manager.resolve(correlation_id, result)
        if not resolved:
            logger.warning(f"No pending future for tool result: {correlation_id[:8]}")

    async def _do_responding(self) -> ReActState:
        """Execute RESPONDING state: send final message and save to memory.

        Returns:
            Next state (COMPLETED or ERROR)
        """
        if self._context is None:
            return ReActState.ERROR

        final_response = self._context.final_response
        if final_response is None:
            return ReActState.ERROR

        logger.info(f"{self.agent_name} completing: {final_response[:50]}...")

        try:
            # Send message
            await self._send_message(final_response, self._context.final_mentions)

            # Save to memory
            if self._last_llm_response:
                await self._save_to_memory(
                    final_response,
                    self._last_llm_response,
                    self._context.all_tool_results,
                )

            return ReActState.COMPLETED
        except Exception as e:
            logger.error(f"Failed to send/save: {e}", exc_info=True)
            return ReActState.ERROR

    async def _do_error_recovery(self) -> ReActState:
        """Execute ERROR state: send error message and stop.

        Returns:
            ERROR (terminal state)
        """
        if self._context is not None:
            # Send error message
            error_msg = "抱歉，处理消息时出现了错误。"
            try:
                await self._send_message(
                    error_msg,
                    [self._context.original_message_sender_id]
                    if self._context.original_message_sender_type == "human"
                    else [],
                )
            except Exception as e:
                logger.error(f"Failed to send error message: {e}")

        logger.warning(f"{self.agent_name} entered ERROR state")
        return ReActState.ERROR

    async def _do_max_iterations(self) -> ReActState:
        """Execute MAX_ITERATIONS state: send timeout message and stop.

        Returns:
            MAX_ITERATIONS (terminal state)
        """
        if self._context is not None:
            logger.warning(f"{self.agent_name} reached max iterations")

            try:
                await self._send_message(
                    "思考时间过长，请简化您的问题。",
                    [self._context.original_message_sender_id]
                    if self._context.original_message_sender_type == "human"
                    else [],
                )
            except Exception as e:
                logger.error(f"Failed to send max iterations message: {e}")

        return ReActState.MAX_ITERATIONS

    async def _do_completed(self) -> ReActState:
        """Execute COMPLETED state: cleanup and stop.

        Returns:
            COMPLETED (terminal state)
        """
        logger.debug(f"{self.agent_name} completed processing")
        return ReActState.COMPLETED

    async def _add_no_tool_reminder(self, response: LLMResponse) -> None:
        """Add reminder when LLM returns no tool calls.

        Args:
            response: The LLM response without tool calls
        """
        if self._context is None:
            return

        # Add assistant message
        if response.content:
            self._context.messages.append(
                {
                    "role": "assistant",
                    "content": response.content,
                }
            )
            self._context.messages.append(
                {
                    "role": "user",
                    "content": "请注意：你必须使用 completeDialog 工具来结束处理并发送最终回复。请不要直接输出文本，而是调用 completeDialog 工具。",
                }
            )
        else:
            self._context.messages.append(
                {
                    "role": "user",
                    "content": '你必须使用 completeDialog 工具来完成对话。请调用 completeDialog(content="你的回复") 来结束。',
                }
            )

    @staticmethod
    def _tool_call_to_dict(tool_call: Any) -> dict[str, Any]:
        """Convert ToolCall to dict for context."""
        import json

        return {
            "id": tool_call.id,
            "type": "function",
            "function": {
                "name": tool_call.function_name,
                "arguments": json.dumps(tool_call.arguments),
            },
        }

    @staticmethod
    def _tool_result_to_string(result: ToolResult) -> str:
        """Convert ToolResult to string for context."""
        if hasattr(result, "to_dict"):
            import json

            return json.dumps(result.to_dict())
        return str(result)
