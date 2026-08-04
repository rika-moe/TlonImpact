"""ReAct loop driver for Agent — stage interpreter (phases 7a/7c).

Steps sessions through the think -> act -> observe cycle and collects
background tool results. Extracted from the Agent god class; collaborates
with the owning Agent through its thin delegates (think, context building,
message sending, memory writes, tracing helpers).

Phase 7a turns the loop into an interpreted program: ``LoopSpec.stages``
is a vector of whitelisted stage forms, executed in order by the stage
registry; ``LoopSpec.until`` is a pure termination expression evaluated
pre-think against loop state. The iron rule stands — the DSL never
executes iteration: the engine owns the loop, including an unconditional
``max_iterations`` hard ceiling that no ``until`` can override. ``act``
means "execute this round's tool calls — all of them"; it is one Python
semantic unit spanning both halves of the step/collect model (dispatch in
:meth:`step_session`, harvest in :meth:`collect_session_results`).

Phase 7c adds auxiliary stages — ``plan`` / ``critique`` / ``revise`` —
and per-stage ``:if`` conditions (pure predicates over loop state and the
session's ``loop_scratch`` scratchpad). Auxiliary stages degrade softly:
an LLM failure inside ``plan``/``critique`` skips the stage with a
warning instead of erroring the session. Whether they run every round is
policy, and policy lives in the program (e.g. ``(plan {:if
(first-iteration?)})``), not in the engine.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from tlon.agents.eval import LOOP_BUILTINS, STAGE_COND_BUILTINS, EvalError, eval_form
from tlon.agents.session import AgentSession, SessionState
from tlon.base.events.event import EventType
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.base.logger import get_logger
from tlon.organization.mail.mail import Mail
from tlon.tools.base import ToolResult

if TYPE_CHECKING:
    from tlon.agents.agent import Agent

logger = get_logger(__name__)


class LoopDriver:
    """Drives the ReAct step/collect cycle for agent sessions."""

    def __init__(self, agent: "Agent"):
        self._agent = agent
        # Stage registry (phases 7a/7c), step side. The collect half of
        # ``act`` is collect_session_results — one semantic unit split by
        # the step/collect asyncio model. Handlers take
        # ``(session, response, opts)`` and chain the round's response.
        self._step_stages: dict[
            str,
            Callable[
                [AgentSession, LLMResponse | None, dict[str, Any]],
                Awaitable[LLMResponse | None],
            ],
        ] = {
            "think": self._stage_think,
            "act": self._stage_act_step,
            "plan": self._stage_plan,
            "critique": self._stage_critique,
            "revise": self._stage_revise,
        }

    async def step_session(self, session: AgentSession, mail: Mail) -> None:
        """Advance a session by one step: until check + stage vector.

        The session's state transitions ACTIVE -> WAITING (tools running).
        When tools complete, collect_session_results transitions back to ACTIVE.
        """
        agent = self._agent

        # Build context on first call (after LLM error: skip rebuild)
        if not session.context:
            session.trigger_message = mail
            context = await agent._build_context(mail)
            session.context = context
            agent._logger.debug(
                "session_context_built", session_id=session.id, message_count=len(context)
            )

        spec = agent.spec.loop

        # Engine hard ceiling: max_iterations is an infrastructure cap,
        # enforced no matter what the until expression says.
        if session.iteration >= spec.max_iterations:
            agent._logger.warning(
                "session_max_iterations",
                session_id=session.id,
                max_iterations=spec.max_iterations,
            )
            await agent._send_mail(
                to=[mail.from_address],
                content="思考时间过长，请简化您的问题。",
                in_reply_to=mail.id,
                thread_id=mail.thread_id,
            )
            session.mark_completed()
            return

        # until expression (phase 7a): evaluated pre-think only.
        if self._until_reached(session):
            await self._terminate_loop(session, mail)
            return

        # Interpret the stage vector in order; stages chain the round's
        # LLM response (think produces it, act consumes it; the auxiliary
        # stages pass it through untouched).
        response: LLMResponse | None = None
        start_iteration = session.iteration
        for stage_form in spec.stages:
            name = str(stage_form[0])
            handler = self._step_stages.get(name)
            if handler is None:  # load-time validation prevents this
                agent._logger.warning(
                    "loop_unknown_stage", session_id=session.id, stage=name
                )
                continue
            opts: dict[str, Any] = stage_form[1] if len(stage_form) > 1 else {}
            if not self._stage_condition_met(session, name, opts):
                continue
            agent._logger.debug("loop_stage_start", session_id=session.id, stage=name)
            response = await handler(session, response, opts)
            if response is None and name == "think":
                # think failed: error recorded; until terminates the loop
                # on a later step.
                return
        # A stage vector without act never advances the iteration on its
        # own; the engine does it so the hard ceiling still bounds such
        # programs. WAITING sessions are excluded — act's collect half
        # advances once the round's tools are harvested.
        if session.state == SessionState.ACTIVE and session.iteration == start_iteration:
            session.advance_iteration()

    def _until_reached(self, session: AgentSession) -> bool:
        """Evaluate the until expression against loop state.

        An evaluation failure falls back to the engine default check
        (iteration / error limits) so the loop always halts.
        """
        agent = self._agent
        spec = agent.spec.loop
        env = {
            "self": {"id": agent.id, "name": agent.config.name},
            "state": {"iteration": session.iteration, "error_count": session.error_count},
            "limits": {
                "max_iterations": spec.max_iterations,
                "max_consecutive_errors": spec.max_consecutive_errors,
            },
        }
        try:
            result = eval_form(spec.until, env, LOOP_BUILTINS)
        except EvalError as e:
            agent._logger.warning(
                "loop_until_eval_failed", session_id=session.id, error=str(e)
            )
            return (
                session.iteration >= spec.max_iterations
                or session.error_count >= spec.max_consecutive_errors
            )
        reached = bool(result)
        agent._logger.debug("loop_until_evaluated", session_id=session.id, reached=reached)
        return reached

    async def _terminate_loop(self, session: AgentSession, mail: Mail) -> None:
        """Terminate a session whose until expression fired.

        The finishing action is chosen by state: error limit -> the error
        mail; anything else -> the iteration-limit mail.
        """
        agent = self._agent
        spec = agent.spec.loop
        if session.error_count >= spec.max_consecutive_errors:
            reason = "error_limit"
            content = "抱歉，处理邮件时出现了错误。"
            agent._logger.warning(
                "session_max_errors",
                session_id=session.id,
                error_count=session.error_count,
                max_errors=spec.max_consecutive_errors,
            )
        else:
            reason = "iteration_limit"
            content = "思考时间过长，请简化您的问题。"
        agent._logger.info("loop_until_reached", session_id=session.id, reason=reason)
        await agent._send_mail(
            to=[mail.from_address],
            content=content,
            in_reply_to=mail.id,
            thread_id=mail.thread_id,
        )
        session.mark_completed()

    # ── Stages (step side) ──

    def _stage_condition_met(
        self, session: AgentSession, name: str, opts: dict[str, Any]
    ) -> bool:
        """Evaluate a stage's optional ``:if`` condition (phase 7c).

        The condition is a pure predicate over loop state and the
        session's loop scratchpad. False skips the stage; an evaluation
        failure also skips it (safe default: never run a stage whose
        gating condition is broken).
        """
        condition = opts.get("if")
        if condition is None:
            return True
        agent = self._agent
        env = {
            "state": {"iteration": session.iteration, "error_count": session.error_count},
            "scratch": session.loop_scratch,
        }
        try:
            result = eval_form(condition, env, STAGE_COND_BUILTINS)
        except EvalError as e:
            agent._logger.warning(
                "loop_stage_condition_failed",
                session_id=session.id,
                stage=name,
                error=str(e),
            )
            return False
        if not result:
            agent._logger.debug(
                "loop_stage_skipped", session_id=session.id, stage=name, reason="condition"
            )
            return False
        return True

    async def _stage_think(
        self,
        session: AgentSession,
        _response: "LLMResponse | None" = None,
        _opts: dict[str, Any] | None = None,
    ) -> "LLMResponse | None":
        """think stage: call the LLM once.

        On failure the error is recorded and the iteration advanced, but
        no mail is sent here — the until expression terminates the loop
        through the unified path on a later step.
        """
        agent = self._agent
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
            return None
        return response

    async def _stage_act_step(
        self,
        session: AgentSession,
        response: "LLMResponse | None",
        _opts: dict[str, Any] | None = None,
    ) -> "LLMResponse | None":
        """act stage, step half: execute this round's tool calls — all of them.

        Covers the no-tool reminder, the sendEmail conflict check, and
        background dispatch; the harvest half is collect_session_results.
        """
        agent = self._agent
        if response is None:
            agent._logger.debug(
                "loop_stage_skipped", session_id=session.id, stage="act", reason="no_response"
            )
            return None

        # No tool calls
        if not response.tool_calls:
            await self._add_no_tool_reminder(session.context, response)
            session.advance_iteration()
            return response

        # Conflict detection
        send_calls = [tc for tc in response.tool_calls if tc.function_name == "sendEmail"]
        other_calls = [tc for tc in response.tool_calls if tc.function_name != "sendEmail"]
        has_complete = any(tc.arguments.get("is_complete") for tc in send_calls)

        if has_complete and other_calls:
            session.context.append({
                "role": "user",
                "content": (
                    "冲突：当 sendEmail 的 is_complete 设为 true 时，"
                    "不得与其他工具同时调用。\n"
                    "请重新规划。"
                ),
            })
            session.advance_iteration()
            return response

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
        return response

    async def _stage_plan(
        self,
        session: AgentSession,
        response: "LLMResponse | None",
        opts: dict[str, Any] | None = None,
    ) -> "LLMResponse | None":
        """plan stage (phase 7c): one planning LLM call before the round.

        The plan text goes into the loop scratchpad and is appended to the
        conversation as a user message. Fails soft: an LLM error skips the
        stage with a warning — no scratch write, no error recorded
        (auxiliary stages degrade, they never blow up the session). Does
        not consume or produce the round's response.
        """
        agent = self._agent
        max_steps = (opts or {}).get("max-steps", 5)
        try:
            async with agent._traced_span("agent.plan"):
                plan_response = await agent.think(
                    session.context
                    + [
                        {
                            "role": "user",
                            "content": (
                                "在开始处理之前，请先制定一个不超过"
                                f" {max_steps} 步的执行计划，每步一行、简短。"
                                "只输出计划本身。"
                            ),
                        }
                    ]
                )
        except Exception as e:
            agent._logger.warning(
                "loop_stage_failed", session_id=session.id, stage="plan", error=str(e)
            )
            return response
        plan_text = plan_response.content or ""
        session.loop_scratch["plan"] = plan_text
        session.context.append({"role": "user", "content": f"[执行计划]\n\n{plan_text}"})
        return response

    async def _stage_critique(
        self,
        session: AgentSession,
        response: "LLMResponse | None",
        _opts: dict[str, Any] | None = None,
    ) -> "LLMResponse | None":
        """critique stage (phase 7c): review the round's latest output.

        Parses the verdict's first line: PASS -> passed; FAIL -> failed
        with the remaining text as feedback; anything unparseable passes
        (lenient default — critique never blocks the main flow). The
        verdict lands in the loop scratchpad. Fails soft like plan; does
        not consume or produce the round's response.
        """
        agent = self._agent
        try:
            async with agent._traced_span("agent.critique"):
                verdict_response = await agent.think(
                    session.context
                    + [
                        {
                            "role": "user",
                            "content": (
                                "请评审上述最新输出是否充分回应了当前邮件。"
                                "第一行只写 PASS 或 FAIL；"
                                "FAIL 时从第二行起给出具体改进意见。"
                            ),
                        }
                    ]
                )
        except Exception as e:
            agent._logger.warning(
                "loop_stage_failed", session_id=session.id, stage="critique", error=str(e)
            )
            return response
        text = (verdict_response.content or "").strip()
        first_line, _, rest = text.partition("\n")
        verdict = first_line.strip()
        if verdict.startswith("FAIL"):
            passed, feedback = False, rest.strip()
        else:
            # PASS or unparseable: lenient default, never block the flow.
            passed, feedback = True, ""
        session.loop_scratch["critique"] = {"passed": passed, "feedback": feedback}
        return response

    async def _stage_revise(
        self,
        session: AgentSession,
        response: "LLMResponse | None",
        _opts: dict[str, Any] | None = None,
    ) -> "LLMResponse | None":
        """revise stage (phase 7c): feed critique feedback back into context.

        No LLM call of its own — the feedback is appended as a user
        message so the next round's think revises naturally. No-op when
        there is no critique verdict or no feedback. Does not consume or
        produce the round's response.
        """
        critique = session.loop_scratch.get("critique")
        feedback = critique.get("feedback") if isinstance(critique, dict) else None
        if feedback:
            session.context.append(
                {"role": "user", "content": f"[评审反馈]\n\n{feedback}\n请据此修正。"}
            )
        return response

    async def collect_session_results(self, session: AgentSession) -> None:
        """Collect completed background tools for a WAITING session.

        This is the collect half of the ``act`` stage (phase 7a).
        Processes results, handles sendEmail, and transitions state
        back to ACTIVE (continue thinking) or COMPLETED (done).
        Completion is event-driven by act (``is_complete``), not a loop
        boundary condition, so it stays outside the until expression.
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

            if tc.function_name == "sendEmail" and result.success:
                content = result.data.get("content", "")
                trigger_mail = session.trigger_message
                # Reply semantics: an explicit `to` wins; otherwise the
                # reply goes back to the sender of the trigger mail, with
                # thread headers inherited from it.
                to = result.data.get("to") or (
                    [trigger_mail.from_address] if trigger_mail else []
                )
                await agent._send_mail(
                    to=to,
                    cc=result.data.get("cc"),
                    subject=result.data.get("subject"),
                    content=content,
                    in_reply_to=trigger_mail.id if trigger_mail else None,
                    thread_id=trigger_mail.thread_id if trigger_mail else None,
                )
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
            # On-complete action expressions (phase 4b): evaluated purely,
            # executed here with full failure isolation — they never affect
            # the session lifecycle. Runs inside the session span, so the
            # spawn quota can attribute spawns to this session.
            await agent._run_on_complete_actions(session, final_content)
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

    async def process_message(self, mail: Mail) -> None:
        """Process a single mail synchronously via inline step/collect loop.

        Used by tests. Creates a session and drives step_session /
        collect_session_results until completion.
        """
        agent = self._agent
        session = AgentSession(
            session_id=f"{agent.id}-msg-{uuid.uuid4().hex[:8]}",
            trigger_content=mail.content,
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
                        await agent._step_session(session, mail)
                    else:
                        break
        finally:
            await session.cancel_pending()
            await agent._mark_mail_read(mail)
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
                        "请使用 sendEmail 工具发送邮件：\n"
                        "- 最终回复：sendEmail(to=['发件人地址'], content='你的回复', is_complete=true)\n"
                        "- 中间进度：sendEmail(to=['发件人地址'], content='进度报告', is_complete=false)，"
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
                        "请使用 sendEmail 工具发送邮件，并设置 is_complete 参数：\n"
                        "- is_complete=true：最终回复，结束本轮\n"
                        "- is_complete=false：中间进度，继续思考"
                    ),
                }
            )
