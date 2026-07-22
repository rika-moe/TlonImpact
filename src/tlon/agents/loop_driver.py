"""ReAct loop driver for Agent.

Steps sessions through the think -> act -> observe cycle and collects
background tool results. Extracted from the Agent god class; collaborates
with the owning Agent through its thin delegates (think, context building,
message sending, memory writes, tracing helpers).
"""

import asyncio
import uuid
from typing import TYPE_CHECKING, Any

from tlon.agents.session import AgentSession, SessionState
from tlon.base.events.event import EventType
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.base.logger import get_logger
from tlon.organization.messaging.message import Message
from tlon.tools.base import ToolResult

if TYPE_CHECKING:
    from tlon.agents.agent import Agent

logger = get_logger(__name__)


class LoopDriver:
    """Drives the ReAct step/collect cycle for agent sessions."""

    def __init__(self, agent: "Agent"):
        self._agent = agent

    async def step_session(self, session: AgentSession, message: Message) -> None:
        """Advance a session by one ReAct step: think + dispatch tools.

        The session's state transitions ACTIVE -> WAITING (tools running).
        When tools complete, collect_session_results transitions back to ACTIVE.
        """
        agent = self._agent

        # Build context on first call (after LLM error: skip rebuild)
        if not session.context:
            context = await agent._build_context(message)
            session.context = context
            agent._logger.debug(
                "session_context_built", session_id=session.id, message_count=len(context)
            )

        max_iterations = agent.config.max_iterations
        max_errors = agent.config.max_consecutive_errors

        # Check iteration limit
        if session.iteration >= max_iterations:
            agent._logger.warning(
                "session_max_iterations", session_id=session.id, max_iterations=max_iterations
            )
            await agent._send_message(
                "思考时间过长，请简化您的问题。",
                mentions=[message.sender_id] if message.sender_type == "human" else [],
            )
            session.mark_completed()
            return

        # Think: call LLM (wrapped in trace span)
        try:
            async with agent._traced_span("agent.think"):
                response = await agent.think(session.context)
            session.set_last_llm_response(response)
        except Exception as e:
            agent._logger.error(
                "session_llm_error", session_id=session.id, error=str(e), exc_info=True
            )
            session.record_error()
            session.advance_iteration()
            if session.error_count >= max_errors:
                agent._logger.warning(
                    "session_max_errors",
                    session_id=session.id,
                    error_count=session.error_count,
                    max_errors=max_errors,
                )
                await agent._send_message(
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
            tasks[tc.id] = asyncio.create_task(self.execute_one_tool(tc))
            await agent.emit(
                EventType.TOOL_EXECUTE_START,
                payload={"agent_id": agent.id, "tool_name": tc.function_name, "arguments": tc.arguments},
                source=agent.id,
            )

        session.dispatch_tools(response.tool_calls, tasks)
        session.mark_waiting()

    async def collect_session_results(self, session: AgentSession) -> None:
        """Collect completed background tools for a WAITING session.

        Processes results, handles sendMessage, and transitions state
        back to ACTIVE (continue thinking) or COMPLETED (done).
        """
        agent = self._agent
        completed = session.collect_completed()
        if not completed:
            return  # still waiting

        is_complete = False
        final_content = ""

        for tc, result in completed:
            session.add_tool_result(tc, result)

            await agent.emit(
                EventType.TOOL_EXECUTE_END,
                payload={"agent_id": agent.id, "tool_name": tc.function_name, "success": result.success},
                source=agent.id,
            )
            if not result.success and result.error:
                await agent.emit(
                    EventType.TOOL_ERROR,
                    payload={"agent_id": agent.id, "tool_name": tc.function_name, "error": result.error},
                    source=agent.id,
                )

            if tc.function_name == "sendMessage" and result.success:
                content = result.data.get("content", "")
                mentions = result.data.get("mentions", [])
                await agent._send_message(content, mentions)
                if result.data.get("is_complete"):
                    is_complete = True
                    final_content = content

            # Append to context
            assistant_msg = {
                "role": "assistant",
                "content": None,
                "tool_calls": [agent._tool_call_to_dict(tc)],
            }
            llm_resp = session.last_llm_response
            if llm_resp and llm_resp.reasoning_content:
                assistant_msg["reasoning_content"] = llm_resp.reasoning_content
            session.context.append(assistant_msg)
            session.context.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": agent._tool_result_to_string(result),
            })

        if is_complete:
            llm_resp = session.last_llm_response
            if llm_resp:
                await agent._save_interaction_to_memory(
                    final_content, llm_resp, session.all_tool_results
                )
            agent._logger.info("session_completed", session_id=session.id, content=final_content)
            session.mark_completed()
        elif not session.has_pending_tools:
            session.advance_iteration()
            session.mark_active()

    async def execute_one_tool(self, tc: ToolCall) -> tuple[ToolCall, ToolResult]:
        """Execute one tool with timeout, returning (tc, result) tuple.

        Catches all exceptions and returns ToolResult(success=False).
        Designed to run via asyncio.create_task in step_session.
        """
        timeout = self._agent._per_tool_timeout
        try:
            return tc, await asyncio.wait_for(
                self._agent.tool_executor.execute(tc.function_name, tc.arguments),
                timeout=timeout,
            )
        except TimeoutError:
            return tc, ToolResult(
                success=False, data={}, error=f"Timeout after {timeout}s"
            )
        except Exception as e:
            return tc, ToolResult(
                success=False, data={}, error=str(e)
            )

    # ── Synchronous message processing (for tests) ──

    async def process_message(self, message: Message) -> None:
        """Process a single message synchronously via inline step/collect loop.

        Used by tests. Creates a session and drives step_session /
        collect_session_results until completion.
        """
        agent = self._agent
        session = AgentSession(
            session_id=f"{agent.id}-msg-{uuid.uuid4().hex[:8]}",
            trigger_content=message.content,
        )

        agent._derive_state({session.id: session})
        try:
            while not session.is_completed:
                with agent._session_span(session):
                    if session.state == SessionState.WAITING:
                        # Yield to let background tool tasks start executing
                        await asyncio.sleep(0)
                        await agent._collect_session_results(session)
                        if session.has_pending_tools:
                            # Wait for all pending tasks to complete, then collect
                            pending = list(session.get_pending_tasks().values())
                            await asyncio.gather(*pending, return_exceptions=True)
                            await agent._collect_session_results(session)
                    elif session.state == SessionState.ACTIVE:
                        await agent._step_session(session, message)
                    else:
                        break
        finally:
            await session.cancel_pending()
            agent._derive_state({})

    async def _add_no_tool_reminder(
        self, context: list[dict[str, Any]], response: LLMResponse
    ) -> None:
        """Add reminder when LLM returns no tool calls."""
        if response.content:
            msg = {"role": "assistant", "content": response.content}
            if response.reasoning_content:
                msg["reasoning_content"] = response.reasoning_content
            context.append(msg)
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
