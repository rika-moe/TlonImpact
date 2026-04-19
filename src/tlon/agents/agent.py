"""Agent implementation for Tlon.

The core intelligent entity that can receive messages, think,
execute tools, and respond.
"""

import asyncio
import contextlib
import json
from enum import Enum
from typing import Any

from tlon.agents.llm_client import LLMClient, LLMResponse, ToolCall
from tlon.agents.prompt_builder import SystemPromptBuilder
from tlon.agents.prompt_context import AgentPromptContext
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.component import Component
from tlon.base.config import AgentConfig
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.base.tracing.trace_context import get_current_trace
from tlon.memory.manager import MemoryManager
from tlon.organization.messaging.message import Message
from tlon.organization.messaging.store import MessageStore
from tlon.tools.base import ToolResult
from tlon.tools.tasks.manager import TaskManager

logger = get_logger(__name__)


class AgentState(Enum):
    """Agent lifecycle states."""

    INITIALIZING = "initializing"  # Starting up
    ACTIVE = "active"  # Running and processing messages
    THINKING = "thinking"  # Currently processing a message
    ERROR = "error"  # Encountered an error
    SHUTDOWN = "shutdown"  # Stopped


class Agent(Component, Device):
    """An intelligent agent that can process messages and execute tools.

    Device: holds an EventBus reference and publishes observability events.
    All infrastructure dependencies are injected via constructor.

    ReAct loop is implemented as a simple async for-loop inside
    _process_message() — no state machine, no EventBus RPC.
    Tool execution is direct: Agent calls tool_executor.execute().

    Example:
        >>> config = AgentConfig(id="dev-001", name="林知遥", ...)
        >>> agent = Agent(config, event_bus, llm_client, memory, ...)
        >>> await agent.initialize()
        >>> # Agent will now respond to messages via EventBus
    """

    def __init__(
        self,
        config: AgentConfig,
        event_bus: EventBus,
        llm_client: LLMClient,
        memory: MemoryManager,
        task_manager: TaskManager,
        tool_executor: ToolExecutor,
        team_members: list["AgentConfig"] | None = None,
        message_store: MessageStore | None = None,
        total_tool_timeout: float = 60.0,
        per_tool_timeout: float = 30.0,
    ):
        """Initialize the agent.

        Args:
            config: Agent configuration
            event_bus: Event bus for publishing/subscribing
            llm_client: LLM client for inference
            memory: Memory manager for long-term storage
            task_manager: Task manager for task tracking
            tool_executor: Tool registry and executor
            team_members: List of all team member configs
            message_store: Message store for querying history
            total_tool_timeout: Total timeout for all tools (deadline)
            per_tool_timeout: Timeout for each individual tool
        """
        Component.__init__(self, name=config.name)
        Device.__init__(self, event_bus=event_bus, name=config.name)

        self.config = config
        self.team_members = team_members or []
        self.message_store = message_store

        self.id = config.id

        # Injected infrastructure dependencies
        self.llm_client = llm_client
        self.memory = memory
        self.task_manager = task_manager
        self.tool_executor = tool_executor

        # Tool execution timeout settings
        self._total_tool_timeout = total_tool_timeout
        self._per_tool_timeout = per_tool_timeout

        # Prompt cache — system prompt is fully static
        self._system_prompt_cache: str | None = None

        # Agent state
        self.state: AgentState | None = None

        # Message queue for serial processing
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._worker_running = False

        logger.info(f"Agent {self.name} ({self.id}) initialized")

    def get_state(self) -> str:
        """Get agent state as string."""
        return self.state.value if self.state else "unknown"

    async def initialize(self) -> None:
        """Initialize the agent."""
        self.state = AgentState.INITIALIZING
        logger.info(f"Initializing agent: {self.name}")

        await self.memory.load()
        self.task_manager.load()

        self.subscribe(EventType.MESSAGE_ROUTED, self._on_message_routed)

        self._worker_running = True
        self._worker_task = asyncio.create_task(self._message_worker())

        self.state = AgentState.ACTIVE
        self._initialized = True
        logger.info(f"Agent {self.name} ready")

    async def shutdown(self) -> None:
        """Shutdown the agent."""
        logger.info(f"Shutting down agent: {self.name}")

        self._worker_running = False
        if self._worker_task:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task

        self.unsubscribe_all()

        self.state = AgentState.SHUTDOWN
        self._initialized = False
        logger.info(f"Agent {self.name} shutdown complete")

    async def _on_message_routed(self, event: Event) -> None:
        """Handle routed message events."""
        from tlon.organization.messaging.router import RoutedMessage

        routed: RoutedMessage = event.payload
        message = routed.original

        if self.id not in routed.target_ids and "@all" not in routed.target_ids:
            return

        if message.sender_id == self.id:
            return

        if not self._should_respond(message):
            return

        await self._message_queue.put(message)
        logger.debug(
            f"{self.name} queued message from {message.sender_name} (reason: {routed.routing_reason})"
        )

    async def _message_worker(self) -> None:
        """Worker task to process messages serially from the queue."""
        logger.info(f"{self.name} message worker started")

        while self._worker_running:
            try:
                message = await self._message_queue.get()
                logger.info(f"{self.name} processing message from {message.sender_name}")

                self.state = AgentState.THINKING

                try:
                    await self._process_message(message)
                except Exception as e:
                    logger.error(f"Error processing message: {e}", exc_info=True)
                    with contextlib.suppress(Exception):
                        await self._send_message(
                            f"抱歉，处理消息时出现了错误: {str(e)[:100]}",
                            mentions=[message.sender_id] if message.sender_type == "human" else [],
                        )
                finally:
                    self._message_queue.task_done()
                    if self.state == AgentState.THINKING:
                        self.state = AgentState.ACTIVE

            except asyncio.CancelledError:
                logger.info(f"{self.name} message worker cancelled")
                break
            except Exception as e:
                logger.error(f"Error in message worker: {e}", exc_info=True)

        logger.info(f"{self.name} message worker stopped")

    def _should_respond(self, message: Message) -> bool:
        """Determine if we should respond to this message."""
        if message.sender_id == self.id:
            return False

        if self._is_mentioned(message):
            logger.debug(f"{self.name} will respond: mentioned by {message.sender_name}")
            return True

        if self.config.proactive and message.sender_type == "human" and not message.mentions:
            logger.info(f"{self.name} will respond: proactive response to {message.sender_name}")
            return True

        logger.debug(f"{self.name} will not respond to message from {message.sender_name}")
        return False

    def _is_mentioned(self, message: Message) -> bool:
        """Check if this agent is mentioned in the message."""
        if self.id in message.mentions:
            return True
        return self.name in message.mentions

    async def _process_message(self, message: Message) -> None:
        """Process a message using ReAct loop.

        Direct async for-loop — no state machine, no EventBus RPC.
        Tool execution calls tool_executor.execute() directly.
        """
        logger.info(f"{self.name} is thinking...")

        await self.memory.check_idle_timeout()
        context = await self._build_context(message)
        logger.debug(f"Context built with {len(context)} messages")

        max_iterations = 10
        max_errors = 3
        error_count = 0
        all_tool_results: list[dict] = []
        last_llm_response: LLMResponse | None = None

        for _iteration in range(max_iterations):
            # Think: call LLM
            try:
                response = await self.think(context)
                last_llm_response = response
            except Exception as e:
                logger.error(f"LLM call failed: {e}", exc_info=True)
                error_count += 1
                if error_count >= max_errors:
                    await self._send_message(
                        "抱歉，处理消息时出现了错误。",
                        mentions=[message.sender_id] if message.sender_type == "human" else [],
                    )
                    return
                continue

            # No tool calls — remind agent to use tools
            if not response.tool_calls:
                await self._add_no_tool_reminder(context, response)
                continue

            # Execute tools directly
            tool_results_list = await self._execute_tool_calls(response.tool_calls)
            all_tool_results.extend([{"tool_call": tc, "result": r} for tc, r in tool_results_list])

            # Check for completeDialog
            complete_dialog_result: ToolResult | None = None
            for tc, result in tool_results_list:
                if tc.function_name == "completeDialog" and result.success:
                    complete_dialog_result = result

            # Build assistant message with tool calls
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": None,
                "tool_calls": [self._tool_call_to_dict(tc) for tc, _ in tool_results_list],
            }
            if response.reasoning_content:
                assistant_msg["reasoning_content"] = response.reasoning_content
            context.append(assistant_msg)

            # Add tool results to context
            for tc, result in tool_results_list:
                context.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": self._tool_result_to_string(result),
                    }
                )

            # If completeDialog was called, send final response and exit
            if complete_dialog_result is not None:
                content = complete_dialog_result.data.get("content", "")
                mentions = complete_dialog_result.data.get("mentions", [])
                await self._send_message(content, mentions)
                if last_llm_response:
                    await self._save_interaction_to_memory(
                        content, last_llm_response, all_tool_results
                    )
                logger.info(f"{self.name} completed: {content[:50]}...")
                return

        # Max iterations reached
        logger.warning(f"{self.name} reached max iterations")
        await self._send_message(
            "思考时间过长，请简化您的问题。",
            mentions=[message.sender_id] if message.sender_type == "human" else [],
        )

    async def _execute_tool_calls(
        self, tool_calls: list[ToolCall]
    ) -> list[tuple[ToolCall, ToolResult]]:
        """Execute tool calls directly with observability.

        Args:
            tool_calls: List of tool calls from LLM

        Returns:
            List of (tool_call, result) tuples
        """
        tool_names = [tc.function_name for tc in tool_calls]
        logger.info(f"{self.name} using {len(tool_calls)} tool(s): {', '.join(tool_names)}")

        async def _execute_one(tc: ToolCall) -> tuple[ToolCall, ToolResult]:
            args_str = str(tc.arguments)
            if len(args_str) > 100:
                args_str = args_str[:100] + "..."
            logger.info(f"  → {tc.function_name}({args_str})")

            await self.emit(
                EventType.TOOL_EXECUTE_START,
                payload={
                    "agent_id": self.id,
                    "tool_name": tc.function_name,
                    "arguments": tc.arguments,
                },
                source=self.id,
            )

            try:
                result = await asyncio.wait_for(
                    self.tool_executor.execute(tc.function_name, tc.arguments),
                    timeout=self._per_tool_timeout,
                )

                await self.emit(
                    EventType.TOOL_EXECUTE_END,
                    payload={
                        "agent_id": self.id,
                        "tool_name": tc.function_name,
                        "success": result.success,
                    },
                    source=self.id,
                )

                msg = (
                    result.message
                    if hasattr(result, "message") and result.message
                    else ("OK" if result.success else "Failed")
                )
                logger.info(f"  ✓ Result: {msg}")
                return tc, result

            except TimeoutError:
                result = ToolResult(
                    success=False,
                    data={},
                    error=f"Timeout after {self._per_tool_timeout}s",
                )
                await self.emit(
                    EventType.TOOL_EXECUTE_END,
                    payload={
                        "agent_id": self.id,
                        "tool_name": tc.function_name,
                        "success": False,
                    },
                    source=self.id,
                )
                logger.error(f"  ✗ Timeout: {tc.function_name}")
                return tc, result

            except Exception as e:
                await self.emit(
                    EventType.TOOL_ERROR,
                    payload={
                        "agent_id": self.id,
                        "tool_name": tc.function_name,
                        "error": str(e),
                    },
                    source=self.id,
                )
                logger.error(f"  ✗ Error: {tc.function_name}: {e}")
                raise

        return await asyncio.gather(*[_execute_one(tc) for tc in tool_calls])

    async def _add_no_tool_reminder(
        self, context: list[dict[str, Any]], response: LLMResponse
    ) -> None:
        """Add reminder when LLM returns no tool calls."""
        if response.content:
            context.append({"role": "assistant", "content": response.content})
            context.append(
                {
                    "role": "user",
                    "content": (
                        "请注意：你必须使用 completeDialog 工具来结束处理并发送最终回复。"
                        "请不要直接输出文本，而是调用 completeDialog 工具。"
                    ),
                }
            )
        else:
            context.append(
                {
                    "role": "user",
                    "content": (
                        "你必须使用 completeDialog 工具来完成对话。"
                        '请调用 completeDialog(content="你的回复") 来结束。'
                    ),
                }
            )

    async def think(self, context: list[dict[str, Any]]) -> LLMResponse:
        """Call LLM to think and generate response."""
        system_prompt = await self._get_system_prompt()
        tools = self.tool_executor.get_tool_definitions()

        current_trace = get_current_trace()

        await self.emit(
            EventType.AGENT_THINKING_START,
            payload={"agent_id": self.id, "agent_name": self.name, "context_length": len(context)},
            source=self.id,
            trace_id=current_trace.trace_id if current_trace else None,
            span_id=current_trace.span_id if current_trace else None,
            parent_span_id=current_trace.parent_span_id if current_trace else None,
        )

        await self.emit(
            EventType.AGENT_LLM_REQUEST,
            payload={
                "agent_id": self.id,
                "model": self.llm_client.config.model_name
                if hasattr(self.llm_client, "config")
                else "unknown",
                "messages_count": len(context),
                "tools_count": len(tools) if tools else 0,
            },
            source=self.id,
            trace_id=current_trace.trace_id if current_trace else None,
            span_id=current_trace.span_id if current_trace else None,
        )

        import time

        start_time = time.time()
        response = await self.llm_client.chat(
            system_prompt=system_prompt,
            messages=context,
            tools=tools if tools else None,
        )
        duration_ms = int((time.time() - start_time) * 1000)

        await self.emit(
            EventType.AGENT_LLM_RESPONSE,
            payload={
                "agent_id": self.id,
                "model": self.llm_client.config.model_name
                if hasattr(self.llm_client, "config")
                else "unknown",
                "duration_ms": duration_ms,
                "finish_reason": response.finish_reason,
                "has_tool_calls": bool(response.tool_calls),
                "tool_call_count": len(response.tool_calls) if response.tool_calls else 0,
            },
            source=self.id,
            trace_id=current_trace.trace_id if current_trace else None,
            span_id=current_trace.span_id if current_trace else None,
        )

        await self.emit(
            EventType.AGENT_THINKING_END,
            payload={"agent_id": self.id, "agent_name": self.name, "duration_ms": duration_ms},
            source=self.id,
            trace_id=current_trace.trace_id if current_trace else None,
            span_id=current_trace.span_id if current_trace else None,
            parent_span_id=current_trace.parent_span_id if current_trace else None,
        )

        return response

    async def _build_knowledge_context(self) -> str:
        """Build knowledge text from memory blocks for user prompt injection."""
        try:
            blocks = await self.memory.build_context_blocks()
            if not blocks:
                return ""
            parts = []
            for block in blocks:
                msg = block.to_openai_message()
                parts.append(msg["content"])
            return "\n\n".join(parts)
        except Exception:
            logger.warning("Failed to build knowledge context from memory blocks")
            return ""

    async def _build_context(self, current_message: Message) -> list[dict[str, Any]]:
        """Build conversation context for LLM."""
        context = []

        knowledge = await self._build_knowledge_context()
        if knowledge:
            context.append({"role": "user", "content": f"[已知信息]\n\n{knowledge}"})

        room_history: list[Message] = []
        if self.message_store:
            room_history = await self.message_store.get_history(
                limit=20, before_id=current_message.id
            )
            logger.debug(f"Retrieved {len(room_history)} messages from store for context")
        else:
            logger.warning("No message store available, context will be limited")

        for msg in room_history:
            if msg.sender_type == "system":
                continue
            if msg.id == current_message.id:
                continue
            if not msg.content:
                continue

            if msg.sender_type == "agent":
                if msg.sender_id == self.id:
                    context.append({"role": "assistant", "content": msg.content})
                else:
                    context.append(
                        {
                            "role": "user",
                            "content": f"[{msg.sender_name}] {msg.content}",
                        }
                    )
            else:
                context.append(
                    {
                        "role": "user",
                        "content": msg.content,
                        "name": msg.sender_id,
                    }
                )

        context.append(
            {
                "role": "user",
                "content": current_message.content,
                "name": current_message.sender_id
                if current_message.sender_type != "agent"
                else None,
            }
        )

        return context

    async def _build_prompt_context(self) -> AgentPromptContext:
        """Build AgentPromptContext by pre-fetching all async data."""
        team_profiles = [(m.name, m.profile) for m in self.team_members if m.id != self.config.id]
        return AgentPromptContext(
            config=self.config,
            team_profiles=team_profiles,
        )

    async def _get_system_prompt(self) -> str:
        """Get system prompt, using cache."""
        if self._system_prompt_cache is None:
            context = await self._build_prompt_context()
            self._system_prompt_cache = SystemPromptBuilder(context).build()
        return self._system_prompt_cache

    async def _save_interaction_to_memory(
        self, final_response: str, llm_response: LLMResponse, tool_results: list
    ) -> None:
        """Save interaction to memory."""
        try:
            msg = Message.create(
                sender_id=self.id,
                sender_name=self.name,
                sender_type="agent",
                content=final_response,
            )
            await self.memory.add_interaction(msg, llm_response, tool_results)
        except Exception as e:
            logger.error(f"Failed to save to memory: {e}")

    async def _send_message(self, content: str, mentions: list[str] | None = None) -> None:
        """Send a message via EventBus."""
        try:
            message = Message.create(
                sender_id=self.id,
                sender_name=self.name,
                sender_type="agent",
                content=content,
                mentions=mentions or [],
            )
            await self.emit(
                EventType.MESSAGE_NEW,
                payload=message,
                source=self.id,
            )
        except Exception as e:
            logger.error(f"Error in _send_message: {e}", exc_info=True)
            raise

    @staticmethod
    def _tool_call_to_dict(tool_call: ToolCall) -> dict[str, Any]:
        """Convert ToolCall to dict for context."""
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
        """Convert tool result to string for context."""
        if hasattr(result, "to_dict"):
            return json.dumps(result.to_dict())
        return str(result)
