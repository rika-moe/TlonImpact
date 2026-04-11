"""Agent implementation for Tlon.

The core intelligent entity that can receive messages, think,
execute tools, and respond.
"""

import asyncio
import contextlib
from enum import Enum
from typing import Any

from tlon.agents.llm_client import LLMClient, LLMResponse, ToolCall
from tlon.agents.prompt_builder import SystemPromptBuilder
from tlon.agents.prompt_context import AgentPromptContext
from tlon.agents.react_state_machine import ReActContext, ReActStateMachine
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.component import Component
from tlon.base.config import AgentConfig, PromptTemplateManager
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.base.tracing import get_current_trace
from tlon.organization.messaging.message import Message
from tlon.organization.messaging.store import MessageStore
from tlon.sandbox.docker_runtime import DockerRuntime
from tlon.subsystem.memory import MemoryManager
from tlon.subsystem.tools.tasks import TaskManager

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
    All infrastructure dependencies are injected via constructor (no internal construction).

    Each agent has:
    - A unique identity and role
    - An isolated Docker runtime environment
    - Long-term memory
    - Access to tools (bash execution, messaging, etc.)

    Example:
        >>> config = AgentConfig(id="dev-001", name="林知遥", ...)
        >>> agent = Agent(config, event_bus, llm_client, docker_runtime, ...)
        >>> await agent.initialize()
        >>> # Agent will now respond to messages via EventBus
    """

    def __init__(
        self,
        config: AgentConfig,
        event_bus: EventBus,
        llm_client: LLMClient,
        docker_runtime: DockerRuntime,
        memory: MemoryManager,
        task_manager: TaskManager,
        prompt_manager: PromptTemplateManager,
        tool_executor: ToolExecutor,
        team_members: list["AgentConfig"] | None = None,
        message_store: MessageStore | None = None,
        total_tool_timeout: float = 60.0,
        per_tool_timeout: float = 30.0,
    ):
        """Initialize the agent.

        Args:
            config: Agent configuration
            event_bus: Event bus for publishing/subscribing (Device dependency)
            llm_client: LLM client for inference
            docker_runtime: Docker sandbox for bash tool execution
            memory: Memory manager for long-term storage
            task_manager: Task manager for task tracking
            prompt_manager: Template manager for system prompt sections
            tool_executor: Executor holding registered tools (Component)
            team_members: List of all team member configs (including inactive ones)
            message_store: Message store for querying history
            total_tool_timeout: Total timeout for all tools (deadline)
            per_tool_timeout: Timeout for each individual tool
        """
        # Initialize both parents
        Component.__init__(self, name=config.name)
        Device.__init__(self, event_bus=event_bus, name=config.name)

        self.config = config
        self.team_members = team_members or []
        self.message_store = message_store

        self.id = config.id
        self.personality = config.personality

        # Injected infrastructure dependencies
        self.llm_client = llm_client
        self.docker_runtime = docker_runtime
        self.memory = memory
        self.task_manager = task_manager
        self._prompt_manager = prompt_manager
        self.tool_executor = tool_executor

        # Tool execution timeout settings
        self._total_tool_timeout = total_tool_timeout
        self._per_tool_timeout = per_tool_timeout

        # Prompt cache — invalidated when memory-modifying tools run
        self._system_prompt_cache: str | None = None
        self._system_prompt_dirty: bool = True

        # Agent state
        self.state: AgentState | None = None

        # Message queue for serial processing
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._worker_running = False

        logger.info(f"Agent {self.name} ({self.id}) initialized")

    def get_state(self) -> str:
        """Get agent state as string.

        Returns:
            State string (initializing, active, thinking, error, shutdown, unknown)
        """
        return self.state.value if self.state else "unknown"

    async def initialize(self) -> None:
        """Initialize the agent.

        Starts Docker container, loads memory,
        and subscribes to events.
        """
        self.state = AgentState.INITIALIZING
        logger.info(f"Initializing agent: {self.name}")

        # Start Docker container
        await self.docker_runtime.ensure_running()
        logger.info(f"Docker container ready for {self.name}")

        # Load memory
        await self.memory.load()

        # Load tasks
        self.task_manager.load()

        # Subscribe to MESSAGE_ROUTED — only receive messages intended for us
        self.subscribe(EventType.MESSAGE_ROUTED, self._on_message_routed)

        # Start message processing worker
        self._worker_running = True
        self._worker_task = asyncio.create_task(self._message_worker())

        self.state = AgentState.ACTIVE
        self._initialized = True  # Mark Component as initialized
        logger.info(f"Agent {self.name} ready")

    async def shutdown(self) -> None:
        """Shutdown the agent.

        Unsubscribes from events and stops Docker container.
        """
        logger.info(f"Shutting down agent: {self.name}")

        # Stop message worker
        self._worker_running = False
        if self._worker_task:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task

        # Unsubscribe from events (Device cleanup)
        self.unsubscribe_all()

        # Stop Docker container (but don't remove - persist for next time)
        await self.docker_runtime.stop()

        self.state = AgentState.SHUTDOWN
        self._initialized = False  # Mark Component as shut down
        logger.info(f"Agent {self.name} shutdown complete")

    async def _on_message_routed(self, event: Event) -> None:
        """Handle routed message events.

        This is called for MESSAGE_ROUTED events. The router has already
        decided which agents should receive this message.

        Args:
            event: The MESSAGE_ROUTED event containing RoutedMessage
        """
        from tlon.organization.messaging.router import RoutedMessage

        routed: RoutedMessage = event.payload
        message = routed.original

        # Check if this agent is a target for this message
        if self.id not in routed.target_ids and "@all" not in routed.target_ids:
            return

        # Ignore own messages
        if message.sender_id == self.id:
            return

        # Additional check: should we respond to this message
        # (router makes initial decision, but agent can still filter)
        if not self._should_respond(message):
            return

        # Queue the message for serial processing
        await self._message_queue.put(message)
        logger.debug(
            f"{self.name} queued message from {message.sender_name} (reason: {routed.routing_reason})"
        )

    async def _message_worker(self) -> None:
        """Worker task to process messages serially from the queue.

        This ensures that an agent processes messages one at a time,
        maintaining order and preventing concurrent processing.
        """
        logger.info(f"{self.name} message worker started")

        while self._worker_running:
            try:
                # Wait for a message
                message = await self._message_queue.get()

                logger.info(f"{self.name} processing message from {message.sender_name}")

                # Set thinking state
                self.state = AgentState.THINKING

                # Process the message
                try:
                    await self._process_message(message)
                except Exception as e:
                    logger.error(f"Error processing message: {e}", exc_info=True)
                    # Try to send an error response
                    with contextlib.suppress(Exception):
                        await self._send_message(
                            f"抱歉，处理消息时出现了错误: {str(e)[:100]}",
                            mentions=[message.sender_id] if message.sender_type == "human" else [],
                        )
                finally:
                    # Mark task as done and restore state
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
        """Determine if we should respond to this message.

        Phase 4 Multi-Agent Response Rules:
        - Proactive agents (proactive=True): Responds to direct user input + when @mentioned
        - Other agents (proactive=False): Only respond when @mentioned

        Args:
            message: The incoming message

        Returns:
            True if should respond
        """
        # Ignore own messages
        if message.sender_id == self.id:
            return False

        # Rule 1: Check if directly mentioned (in mentions list or content)
        if self._is_mentioned(message):
            logger.debug(f"{self.name} will respond: mentioned by {message.sender_name}")
            return True

        # Rule 2: Proactive agents respond to direct human input (no mentions)
        logger.debug(
            f"{self.name} checking proactive: proactive={self.config.proactive}, "
            f"sender_type={message.sender_type}, mentions={message.mentions}"
        )

        if self.config.proactive and message.sender_type == "human" and not message.mentions:
            # Check if message has no mentions (direct user input)
            logger.info(f"{self.name} will respond: proactive response to {message.sender_name}")
            return True

        # Not applicable - don't respond
        logger.debug(f"{self.name} will not respond to message from {message.sender_name}")
        return False

    def _is_mentioned(self, message: Message) -> bool:
        """Check if this agent is mentioned in the message.

        Only checks the message.mentions list (set by tools like completeDialog).
        Does NOT parse content for @mentions.

        - "林知遥" matches mention "林知遥" (exact)
        - "dev-001" matches mention "dev-001" (exact)

        Args:
            message: The message to check

        Returns:
            True if this agent is mentioned
        """
        # Check exact ID match in mentions list
        if self.id in message.mentions:
            return True

        # Check exact name match in mentions list
        return self.name in message.mentions

    async def _process_message(self, message: Message) -> None:
        """Process a message using ReAct State Machine.

        Replaces the implicit for-loop with an explicit state machine
        that emits state change events for observability.

        Args:
            message: The message to process
        """
        logger.info(f"{self.name} is thinking...")

        # Check idle timeout before processing
        await self.memory.check_idle_timeout()

        # Build context
        messages = await self._build_context(message)
        logger.debug(f"Context built with {len(messages)} messages")

        # Get trace context for observability
        current_trace = get_current_trace()

        # Create ReAct context
        react_context = ReActContext(
            messages=messages,
            original_message_id=message.id,
            original_message_sender_id=message.sender_id,
            original_message_sender_type=message.sender_type,
            max_iterations=10,
            trace_id=current_trace.trace_id if current_trace else None,
            span_id=current_trace.span_id if current_trace else None,
        )

        # Create state machine with callbacks
        state_machine = ReActStateMachine(
            agent_id=self.id,
            agent_name=self.name,
            llm_client=self.llm_client,
            event_bus=self.event_bus,
            system_prompt_provider=self._get_system_prompt,
            tool_defs_provider=self.tool_executor.get_tool_definitions,
            send_message_callback=lambda content, mentions: self._send_final_message(
                content, mentions, message
            ),
            save_to_memory_callback=self._save_interaction_to_memory,
            total_tool_timeout=self._total_tool_timeout,
            per_tool_timeout=self._per_tool_timeout,
        )

        # Run state machine to completion
        try:
            final_state = await state_machine.start(react_context)
            logger.info(f"{self.name} finished processing with state: {final_state.value}")
        except Exception as e:
            logger.error(f"State machine failed: {e}", exc_info=True)
            # Fallback error handling
            await self._send_message(
                "抱歉，处理消息时出现了内部错误。",
                mentions=[message.sender_id] if message.sender_type == "human" else [],
            )

    async def _send_final_message(
        self, content: str, mentions: list[str], original_message: Message
    ) -> None:
        """Send final response message via EventBus.

        Args:
            content: Message content
            mentions: List of mentions
            original_message: Original message being responded to
        """
        from tlon.organization.messaging.message import Message

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
        logger.info(f"{self.name} response sent successfully via EventBus")

    async def _save_interaction_to_memory(
        self, final_response: str, llm_response: LLMResponse, tool_results: list
    ) -> None:
        """Save interaction to memory.

        Args:
            final_response: Final response content
            llm_response: The LLM response object
            tool_results: List of tool execution results
        """
        try:
            # Create a minimal message object for memory storage
            from tlon.organization.messaging.message import Message

            msg = Message.create(
                sender_id=self.id,
                sender_name=self.name,
                sender_type="agent",
                content=final_response,
            )
            await self.memory.add_interaction(msg, llm_response, tool_results)
        except Exception as e:
            logger.error(f"Failed to save to memory: {e}")

    async def think(self, context: list[dict[str, Any]]) -> LLMResponse:
        """Call LLM to think and generate response.

        Args:
            context: Conversation context

        Returns:
            LLM response
        """
        system_prompt = await self._get_system_prompt()
        tools = self.tool_executor.get_tool_definitions()

        # Get current trace context if available
        current_trace = get_current_trace()

        # Emit thinking start event
        await self.emit(
            EventType.AGENT_THINKING_START,
            payload={
                "agent_id": self.id,
                "agent_name": self.name,
                "context_length": len(context),
            },
            source=self.id,
            trace_id=current_trace.trace_id if current_trace else None,
            span_id=current_trace.span_id if current_trace else None,
            parent_span_id=current_trace.parent_span_id if current_trace else None,
        )

        # Emit LLM request event
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

        # Call LLM
        import time

        start_time = time.time()
        response = await self.llm_client.chat(
            system_prompt=system_prompt,
            messages=context,
            tools=tools if tools else None,
        )
        duration_ms = int((time.time() - start_time) * 1000)

        # Emit LLM response event
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

        # Emit thinking end event
        await self.emit(
            EventType.AGENT_THINKING_END,
            payload={
                "agent_id": self.id,
                "agent_name": self.name,
                "duration_ms": duration_ms,
            },
            source=self.id,
            trace_id=current_trace.trace_id if current_trace else None,
            span_id=current_trace.span_id if current_trace else None,
            parent_span_id=current_trace.parent_span_id if current_trace else None,
        )

        return response

    async def _build_context(self, current_message: Message) -> list[dict[str, Any]]:
        """Build conversation context for LLM.

        Builds context from:
        1. MessageStore history (all messages in the room)
        2. Agent's own memory (interactions it processed)

        Args:
            current_message: The current message to respond to

        Returns:
            List of messages in OpenAI format
        """
        context = []

        # Get recent messages from MessageStore
        # This ensures Agent has context even after restart
        room_history: list[Message] = []
        if self.message_store:
            room_history = await self.message_store.get_history(
                limit=20, before_id=current_message.id
            )
            logger.debug(f"Retrieved {len(room_history)} messages from store for context")
        else:
            logger.warning("No message store available, context will be limited")

        # Convert messages to OpenAI format
        for msg in room_history:
            # Skip system messages (join/leave notifications)
            if msg.sender_type == "system":
                continue

            # Skip the current message (will be added at the end)
            if msg.id == current_message.id:
                continue

            # Skip empty messages
            if not msg.content:
                continue

            if msg.sender_type == "agent":
                # Agent message - check if it's from this agent
                if msg.sender_id == self.id:
                    context.append(
                        {
                            "role": "assistant",
                            "content": msg.content,
                        }
                    )
                else:
                    # Message from other agents - treat as user context
                    context.append(
                        {
                            "role": "user",
                            "content": f"[{msg.sender_name}] {msg.content}",
                        }
                    )
            else:
                # Human message
                context.append(
                    {
                        "role": "user",
                        "content": msg.content,
                        "name": msg.sender_id,
                    }
                )

        # Add current message
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
        """Build AgentPromptContext by pre-fetching all async data.

        Returns:
            AgentPromptContext with all data needed for SystemPromptBuilder
        """
        memory_blocks = await self.memory.build_context_blocks()
        return AgentPromptContext(
            config=self.config,
            team_members=self.team_members,
            tool_definitions=self.tool_executor.get_tool_definitions(),
            memory_blocks=memory_blocks,
            prompt_manager=self._prompt_manager,
        )

    async def _get_system_prompt(self) -> str:
        """Get system prompt, using cache when not dirty.

        The cache is invalidated when memory-modifying tools run
        (rememberFact, forget, generateSummary).

        Returns:
            System prompt string
        """
        if self._system_prompt_dirty or self._system_prompt_cache is None:
            context = await self._build_prompt_context()
            self._system_prompt_cache = SystemPromptBuilder(context).build()
            self._system_prompt_dirty = False
        return self._system_prompt_cache

    async def _execute_tool_call(self, tool_call: ToolCall) -> Any:
        """Execute a tool call and publish observability events.

        Agent is the Device responsible for publishing tool events;
        ToolExecutor (Non-Device) holds no EventBus reference.

        Args:
            tool_call: The tool call to execute

        Returns:
            Tool execution result
        """
        logger.debug(f"Executing tool: {tool_call.function_name}")

        await self.emit(
            EventType.TOOL_EXECUTE_START,
            payload={
                "agent_id": self.id,
                "tool_name": tool_call.function_name,
                "arguments": tool_call.arguments,
            },
            source=self.id,
        )

        try:
            result = await self.tool_executor.execute(tool_call.function_name, tool_call.arguments)

            # Invalidate prompt cache for memory-modifying tools
            if tool_call.function_name in ("rememberFact", "forget", "generateSummary"):
                self._system_prompt_dirty = True

            await self.emit(
                EventType.TOOL_EXECUTE_END,
                payload={
                    "agent_id": self.id,
                    "tool_name": tool_call.function_name,
                    "success": result.success,
                },
                source=self.id,
            )

            return result

        except Exception as e:
            await self.emit(
                EventType.TOOL_ERROR,
                payload={
                    "agent_id": self.id,
                    "tool_name": tool_call.function_name,
                    "error": str(e),
                },
                source=self.id,
            )
            raise

    async def _send_message(self, content: str, mentions: list[str] | None = None) -> None:
        """Send a message via EventBus.

        Args:
            content: Message content
            mentions: Optional list of user IDs to mention
        """
        try:
            from tlon.organization.messaging.message import Message

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

    def _tool_call_to_dict(self, tool_call: ToolCall) -> dict[str, Any]:
        """Convert ToolCall to dict for context."""
        return {
            "id": tool_call.id,
            "type": "function",
            "function": {
                "name": tool_call.function_name,
                "arguments": __import__("json").dumps(tool_call.arguments),
            },
        }

    def _tool_result_to_string(self, result: Any) -> str:
        """Convert tool result to string for context."""
        if hasattr(result, "to_dict"):
            return __import__("json").dumps(result.to_dict())
        return str(result)

    def _extract_mentions(self, content: str) -> list[str]:
        """Extract @mentions from content."""
        import re

        pattern = r'@"([^"]+)"|@(\S+)'
        matches = re.findall(pattern, content)
        mentions = []
        for match in matches:
            mentions.extend([m for m in match if m])
        return mentions
