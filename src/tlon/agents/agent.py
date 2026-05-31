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
from tlon.agents.session import AgentSession, SessionState
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

    ReAct loop is implemented via step/collect scheduling in _main_loop()
    with multi-session support. Tool execution uses background asyncio tasks.

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

        # Tool execution timeout
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
        self._worker_task = asyncio.create_task(self._main_loop())

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

    async def _main_loop(self) -> None:
        """Agent's continuous main loop with multi-session scheduling.

        Cycles through all active sessions each pass:
        1. New messages -> create/resume sessions
        2. ACTIVE sessions -> one think+dispatch step
        3. WAITING sessions -> collect completed background tools
        4. COMPLETED -> cleanup
        """
        logger.info(f"{self.name} main loop started")
        sessions: dict[str, AgentSession] = {}
        session_messages: dict[str, Message] = {}

        while self._worker_running:
            # ── 1. New messages ──
            try:
                msg = self._message_queue.get_nowait()
                sid = f"{self.id}-{msg.id}"
                session = AgentSession(sid, msg.content)
                sessions[sid] = session
                session_messages[sid] = msg
                logger.info(f"{self.name} new session {sid}: {msg.content[:50]}")
            except asyncio.QueueEmpty:
                # No new messages — if no sessions either, block
                if not sessions:
                    try:
                        msg = await asyncio.wait_for(self._message_queue.get(), timeout=0.5)
                        sid = f"{self.id}-{msg.id}"
                        sessions[sid] = AgentSession(sid, msg.content)
                        session_messages[sid] = msg
                    except TimeoutError:
                        continue

            # ── 2 & 3. Advance sessions ──
            for sid in list(sessions.keys()):
                session = sessions[sid]
                if session.id not in session_messages:
                    continue

                try:
                    if session.state == SessionState.WAITING:
                        await self._collect_session_results(session)
                    elif session.state == SessionState.ACTIVE:
                        msg = session_messages[sid]
                        await self._step_session(session, msg)
                except Exception as e:
                    logger.error(f"Session {sid} error: {e}", exc_info=True)
                    session.mark_completed()

                # ── 4. Cleanup ──
                if session.is_completed:
                    del sessions[sid]
                    session_messages.pop(sid, None)

            # Yield control
            await asyncio.sleep(0.01 if sessions else 0.05)

        logger.info(f"{self.name} main loop stopped")

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

    async def _step_session(self, session: AgentSession, message: Message) -> None:
        """Advance a session by one ReAct step: think + dispatch tools.

        The session's state transitions ACTIVE -> WAITING (tools running).
        When tools complete, _collect_session_results transitions back to ACTIVE.
        """
        self.state = AgentState.THINKING

        # Build context on first call (after LLM error: skip rebuild)
        if not session.context:
            await self.memory.check_idle_timeout()
            context = await self._build_context(message)
            session.context = context
            logger.debug(f"Session {session.id}: context built with {len(context)} messages")

        max_iterations = 10
        max_errors = 3

        # Check iteration limit
        if session.iteration >= max_iterations:
            logger.warning(f"Session {session.id}: max iterations reached")
            await self._send_message(
                "思考时间过长，请简化您的问题。",
                mentions=[message.sender_id] if message.sender_type == "human" else [],
            )
            session.mark_completed()
            return

        # Think: call LLM
        try:
            response = await self.think(session.context)
            session.set_last_llm_response(response)
        except Exception as e:
            logger.error(f"Session {session.id}: LLM call failed: {e}", exc_info=True)
            session.record_error()
            session.advance_iteration()
            if session.error_count >= max_errors:
                await self._send_message(
                    "抱歉，处理消息时出现了错误。",
                    mentions=[message.sender_id] if message.sender_type == "human" else [],
                )
                session.mark_completed()
            return

        # No tool calls
        if not response.tool_calls:
            await self._add_no_tool_reminder(session.context, response)
            session.advance_iteration()
            return

        # Conflict detection
        send_calls = [tc for tc in response.tool_calls if tc.function_name == "sendMessage"]
        other_calls = [tc for tc in response.tool_calls if tc.function_name != "sendMessage"]
        has_complete = any(tc.arguments.get("is_complete") for tc in send_calls)

        if has_complete and other_calls:
            session.context.append({
                "role": "user",
                "content": (
                    "冲突：当 sendMessage 的 is_complete 设为 true 时，"
                    "不得与其他工具同时调用。\n"
                    "请重新规划。"
                ),
            })
            session.advance_iteration()
            return

        # ── Dispatch tools as background tasks ──
        tasks = {}
        for tc in response.tool_calls:
            tasks[tc.id] = asyncio.create_task(self._execute_one_tool(tc))
            await self.emit(
                EventType.TOOL_EXECUTE_START,
                payload={"agent_id": self.id, "tool_name": tc.function_name, "arguments": tc.arguments},
                source=self.id,
            )

        session.dispatch_tools(response.tool_calls, tasks)
        session.mark_waiting()

    async def _collect_session_results(self, session: AgentSession) -> None:
        """Collect completed background tools for a WAITING session.

        Processes results, handles sendMessage, and transitions state
        back to ACTIVE (continue thinking) or COMPLETED (done).
        """
        completed = session.collect_completed()
        if not completed:
            return  # still waiting

        is_complete = False
        final_content = ""

        for tc, result in completed:
            session.add_tool_result(tc, result)

            await self.emit(
                EventType.TOOL_EXECUTE_END,
                payload={"agent_id": self.id, "tool_name": tc.function_name, "success": result.success},
                source=self.id,
            )
            if not result.success and result.error:
                await self.emit(
                    EventType.TOOL_ERROR,
                    payload={"agent_id": self.id, "tool_name": tc.function_name, "error": result.error},
                    source=self.id,
                )

            if tc.function_name == "sendMessage" and result.success:
                content = result.data.get("content", "")
                mentions = result.data.get("mentions", [])
                await self._send_message(content, mentions)
                if result.data.get("is_complete"):
                    is_complete = True
                    final_content = content

            # Append to context
            assistant_msg = {
                "role": "assistant",
                "content": None,
                "tool_calls": [self._tool_call_to_dict(tc)],
            }
            session.context.append(assistant_msg)
            session.context.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": self._tool_result_to_string(result),
            })

        if is_complete:
            llm_resp = session.last_llm_response
            if llm_resp:
                await self._save_interaction_to_memory(
                    final_content, llm_resp, session.all_tool_results
                )
            logger.info(f"Session {session.id} completed: {final_content[:50]}")
            session.mark_completed()
        elif not session.has_pending_tools:
            session.advance_iteration()
            session.mark_active()

    async def _execute_one_tool(self, tc: ToolCall) -> tuple[ToolCall, ToolResult]:
        """Execute one tool with timeout, returning (tc, result) tuple.

        Catches all exceptions and returns ToolResult(success=False).
        Designed to run via asyncio.create_task in _step_session.
        """
        try:
            return tc, await asyncio.wait_for(
                self.tool_executor.execute(tc.function_name, tc.arguments),
                timeout=self._per_tool_timeout,
            )
        except TimeoutError:
            return tc, ToolResult(
                success=False, data={}, error=f"Timeout after {self._per_tool_timeout}s"
            )
        except Exception as e:
            return tc, ToolResult(
                success=False, data={}, error=str(e)
            )

    # ── Synchronous message processing (for tests) ──

    async def _process_message(self, message: Message) -> None:
        """Process a single message synchronously via inline step/collect loop.

        Used by tests. Creates a session and drives _step_session /
        _collect_session_results until completion.
        """
        import uuid

        session = AgentSession(
            session_id=f"{self.id}-msg-{uuid.uuid4().hex[:8]}",
            trigger_content=message.content,
        )

        while not session.is_completed:
            if session.state == SessionState.WAITING:
                # Yield to let background tool tasks start executing
                await asyncio.sleep(0)
                await self._collect_session_results(session)
                if session.has_pending_tools:
                    # Wait for all pending tasks to complete, then collect
                    pending = list(session.get_pending_tasks().values())
                    await asyncio.gather(*pending, return_exceptions=True)
                    await self._collect_session_results(session)
            elif session.state == SessionState.ACTIVE:
                await self._step_session(session, message)
            else:
                break

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
                        "注意：你必须调用至少一个工具，禁止直接输出文本。\n"
                        "请使用 sendMessage 工具发送消息：\n"
                        "- 最终回复：sendMessage(content='你的回复', is_complete=true)\n"
                        "- 中间进度：sendMessage(content='进度报告', is_complete=false)，"
                        "然后继续调用其他工具。"
                    ),
                }
            )
        else:
            context.append(
                {
                    "role": "user",
                    "content": (
                        "你必须调用至少一个工具。"
                        "请使用 sendMessage 工具来发送消息，并设置 is_complete 参数：\n"
                        "- is_complete=true：最终回复，结束本轮\n"
                        "- is_complete=false：中间进度，继续思考"
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
        except Exception:
            logger.error("Failed to emit MESSAGE_NEW event", exc_info=True)

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
