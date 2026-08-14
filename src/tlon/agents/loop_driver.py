"""ReAct loop driver for Agent — stage interpreter skeleton (phases 7a/7c).

Steps sessions through the think -> act -> observe cycle and collects
background tool results. The driver runs on injected capabilities only
(C2): identity/spec as plain data, :class:`StageServices` for
queries/observability, :class:`EffectExecutor` for the external effects
declared in stage outcomes, plus the tool executor, the on-complete
runner, and a state-derivation closure. There is no host/Agent
back-reference — the Agent <-> LoopDriver runtime cycle is broken.

``LoopSpec.stages`` is a vector of whitelisted stage forms executed in
order by the stage registry; ``LoopSpec.until`` is a pure termination
expression evaluated pre-think. The engine owns the loop, including an
unconditional ``max_iterations`` hard ceiling that no ``until`` can
override; stage run policy lives in the program (``:if``), not the engine.

This module keeps the interpreter skeleton: registry, step/collect
scheduling shells, until/:if evaluators, the tool execution primitive,
and the Outcome application mechanics. Stage implementations live in
:mod:`tlon.agents.stages` (R1).

Effects-as-data (C1/C2): every stage is a pure transformation
``Session x Services -> StageOutcome`` declaring its intended state
changes; the driver applies session-local fields directly and executes
external effects through the EffectExecutor. ``act`` only declares the
dispatch list — the concurrency (one background task per tool call) is
the engine's.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from tlon.agents.eval import (
    DEFAULT_FUEL,
    LOOP_BUILTINS,
    STAGE_COND_BUILTINS,
    EvalError,
    eval_form,
)
from tlon.agents.executor import EffectExecutor
from tlon.agents.member_spec import MemberLoop, MemberRule
from tlon.agents.on_complete import OnCompleteRunner
from tlon.agents.session import AgentSession, SessionState
from tlon.agents.stages.act import act_stage_collect, act_stage_step
from tlon.agents.stages.critique import critique_stage
from tlon.agents.stages.outcome import MailEffect, StageOutcome
from tlon.agents.stages.plan import plan_stage
from tlon.agents.stages.revise import revise_stage
from tlon.agents.stages.services import StageServices
from tlon.agents.stages.think import think_stage
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.trigger import AgentIdentity
from tlon.base.events.event import EventType
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.base.logger import get_logger
from tlon.organization.mail.mail import Mail
from tlon.tools.base import ToolResult

logger = get_logger(__name__)

class LoopDriver:
    """Drives the ReAct step/collect cycle for agent sessions."""

    def __init__(
        self,
        identity: AgentIdentity,
        rule: MemberRule,
        services: StageServices,
        executor: EffectExecutor,
        tool_executor: ToolExecutor,
        per_tool_timeout: float,
        on_complete: OnCompleteRunner,
    ):
        self._identity = identity
        # A loop driver serves a cognitive agent — its rule carries a loop.
        assert rule.loop is not None, "LoopDriver requires a cognitive rule"
        self._rule = rule
        self._loop: MemberLoop = rule.loop
        # Phase D: the loop's self-declared :fuel caps the DSL evaluation of
        # its :until / stage conditions (None = engine default). The loop's
        # control flow (max_iterations, stage whitelist, until timing) is
        # untouched.
        self._loop_fuel = self._loop.fuel if self._loop.fuel is not None else DEFAULT_FUEL
        self._services = services
        self._executor = executor
        self._tool_executor = tool_executor
        self._per_tool_timeout = per_tool_timeout
        self._on_complete = on_complete
        # Stage registry (phases 7a/7c), step side. The collect half of
        # ``act`` is collect_session_results — one semantic unit split by
        # the step/collect asyncio model. Handlers take
        # ``(session, response, opts)`` and return a StageOutcome the
        # driver applies (C1/C2).
        self._stages: dict[
            str,
            Callable[
                [AgentSession, LLMResponse | None, dict[str, Any]],
                Awaitable[StageOutcome],
            ],
        ] = {
            "think": partial(think_stage, services),
            "plan": partial(plan_stage, services),
            "critique": partial(critique_stage, services),
            "revise": partial(revise_stage, services),
            "act": partial(act_stage_step, services),
        }

    async def step_session(self, session: AgentSession, mail: Mail) -> None:
        """Advance a session by one step: until check + stage vector.

        The session's state transitions ACTIVE -> WAITING (tools running).
        When tools complete, collect_session_results transitions back to ACTIVE.
        """
        log = self._services.logger

        # Build context on first call (after LLM error: skip rebuild)
        if not session.context:
            session.trigger_message = mail
            context = await self._services.build_context(mail)
            session.context = context
            log.debug(
                "session_context_built", session_id=session.id, message_count=len(context)
            )

        loop = self._loop

        # Engine hard ceiling: max_iterations is an infrastructure cap,
        # enforced no matter what the until expression says.
        if session.iteration >= loop.max_iterations:
            log.warning(
                "session_max_iterations",
                session_id=session.id,
                max_iterations=loop.max_iterations,
            )
            await self._executor.send_mail(
                MailEffect(
                    to=[mail.from_address],
                    content="思考时间过长，请简化您的问题。",
                    in_reply_to=mail.id,
                    thread_id=mail.thread_id,
                )
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
        for stage_form in loop.stages:
            name = str(stage_form[0])
            opts: dict[str, Any] = stage_form[1] if len(stage_form) > 1 else {}
            handler = self._stages.get(name)
            if handler is None:
                # load-time validation prevents this
                log.warning("loop_unknown_stage", session_id=session.id, stage=name)
                continue
            if not self._stage_condition_met(session, name, opts):
                continue
            log.debug("loop_stage_start", session_id=session.id, stage=name)
            outcome = await handler(session, response, opts)
            response = await self._apply_outcome(session, response, outcome)
            if name == "think" and outcome.response is None:
                # think failed: error recorded; until terminates the
                # loop on a later step.
                return
        # A stage vector without act never advances the iteration on its
        # own; the engine does it so the hard ceiling still bounds such
        # programs. WAITING sessions are excluded — act's collect half
        # advances once the round's tools are harvested.
        if session.state == SessionState.ACTIVE and session.iteration == start_iteration:
            session.advance_iteration()

    async def _apply_outcome(
        self, session: AgentSession, response: LLMResponse | None, outcome: StageOutcome
    ) -> LLMResponse | None:
        """Apply a stage's declared state changes to the session (C1/C2).

        The stage computes; the engine mutates. Session-local fields
        land directly; external effects go through the EffectExecutor.
        A non-None ``outcome.response`` updates the round's response
        chain and is recorded as the session's last LLM response.
        Application order: context -> scratch -> counters/state ->
        tool results -> response chain -> tool dispatch -> mails ->
        interaction save -> on-complete actions -> completion —
        equivalent to the pre-C2 interleaving (tool results land before
        the save so the saved interaction sees this round's results;
        save -> on-complete -> mark_completed stays inside the session
        span, so spawn quota attribution is unchanged).
        """
        if outcome.context_appends:
            session.context.extend(outcome.context_appends)
        if outcome.scratch_updates:
            session.loop_scratch.update(outcome.scratch_updates)
        if outcome.record_error:
            session.record_error()
        if outcome.advance_iteration:
            session.advance_iteration()
        if outcome.mark_active:
            session.mark_active()
        for tc, result in outcome.tool_results:
            session.add_tool_result(tc, result)
        if outcome.response is not None:
            session.set_last_llm_response(outcome.response)
            response = outcome.response
        if outcome.tool_dispatches:
            # Declared by act; the concurrency is the engine's: one
            # background task and one TOOL_EXECUTE_START event per tool.
            tasks = {}
            for tc in outcome.tool_dispatches:
                tasks[tc.id] = asyncio.create_task(self.execute_one_tool(tc))
                await self._executor.publish(
                    EventType.TOOL_EXECUTE_START,
                    payload={
                        "agent_id": self._identity.id,
                        "tool_name": tc.function_name,
                        "arguments": tc.arguments,
                    },
                    source=self._identity.id,
                )
            session.dispatch_tools(outcome.tool_dispatches, tasks)
            session.mark_waiting()
        for effect in outcome.mails:
            await self._executor.send_mail(effect)
        if outcome.save_interaction is not None:
            await self._executor.save_interaction(
                outcome.save_interaction, session.last_llm_response, session.all_tool_results
            )
        if outcome.run_on_complete:
            await self._on_complete.run(session, outcome.save_interaction or "")
        if outcome.complete:
            session.mark_completed()
        return response

    def _until_reached(self, session: AgentSession) -> bool:
        """Evaluate the until expression against loop state.

        An evaluation failure falls back to the engine default check
        (iteration / error limits) so the loop always halts.
        """
        log = self._services.logger
        loop = self._loop
        env = {
            "self": {"id": self._identity.id, "name": self._identity.name},
            "state": {"iteration": session.iteration, "error_count": session.error_count},
            "limits": {
                "max_iterations": loop.max_iterations,
                "max_consecutive_errors": loop.max_consecutive_errors,
            },
        }
        try:
            result = eval_form(loop.until, env, LOOP_BUILTINS, fuel=self._loop_fuel)
        except EvalError as e:
            log.warning("loop_until_eval_failed", session_id=session.id, error=str(e))
            return session.iteration >= loop.max_iterations or (
                session.error_count >= loop.max_consecutive_errors
            )
        reached = bool(result)
        log.debug("loop_until_evaluated", session_id=session.id, reached=reached)
        return reached

    async def _terminate_loop(self, session: AgentSession, mail: Mail) -> None:
        """Terminate a session whose until expression fired.

        The finishing action is chosen by state: error limit -> the error
        mail; anything else -> the iteration-limit mail.
        """
        log = self._services.logger
        loop = self._loop
        if session.error_count >= loop.max_consecutive_errors:
            reason = "error_limit"
            content = "抱歉，处理邮件时出现了错误。"
            log.warning(
                "session_max_errors",
                session_id=session.id,
                error_count=session.error_count,
                max_errors=loop.max_consecutive_errors,
            )
        else:
            reason = "iteration_limit"
            content = "思考时间过长，请简化您的问题。"
        log.info("loop_until_reached", session_id=session.id, reason=reason)
        await self._executor.send_mail(
            MailEffect(
                to=[mail.from_address],
                content=content,
                in_reply_to=mail.id,
                thread_id=mail.thread_id,
            )
        )
        session.mark_completed()

    def _stage_condition_met(self, session: AgentSession, name: str, opts: dict[str, Any]) -> bool:
        """Evaluate a stage's optional ``:if`` condition (phase 7c).

        The condition is a pure predicate over loop state and the
        session's loop scratchpad. False skips the stage; an evaluation
        failure also skips it (safe default: never run a stage whose
        gating condition is broken).
        """
        condition = opts.get("if")
        if condition is None:
            return True
        log = self._services.logger
        env = {
            "state": {"iteration": session.iteration, "error_count": session.error_count},
            "scratch": session.loop_scratch,
        }
        try:
            result = eval_form(
                condition, env, STAGE_COND_BUILTINS, fuel=self._loop_fuel
            )
        except EvalError as e:
            log.warning(
                "loop_stage_condition_failed",
                session_id=session.id,
                stage=name,
                error=str(e),
            )
            return False
        if not result:
            log.debug(
                "loop_stage_skipped", session_id=session.id, stage=name, reason="condition"
            )
            return False
        return True

    async def collect_session_results(self, session: AgentSession) -> None:
        """Collect completed background tools for a WAITING session.

        Scheduling shell for the collect half of the ``act`` stage
        (phase 7a). The harvest (``session.collect_completed``) is the
        engine's; the results are handed to
        :func:`tlon.agents.stages.act.act_stage_collect`, whose declared
        Outcome the engine applies here.
        """
        completed = session.collect_completed()
        outcome = await act_stage_collect(self._services, session, completed)
        await self._apply_outcome(session, None, outcome)

    async def execute_one_tool(self, tc: ToolCall) -> tuple[ToolCall, ToolResult]:
        """Execute one tool with timeout, returning (tc, result) tuple.

        Catches all exceptions and returns ToolResult(success=False).
        Designed to run via asyncio.create_task in step_session.
        """
        timeout = self._per_tool_timeout
        try:
            return tc, await asyncio.wait_for(
                self._tool_executor.execute(tc.function_name, tc.arguments),
                timeout=timeout,
            )
        except TimeoutError:
            return tc, ToolResult(success=False, data={}, error=f"Timeout after {timeout}s")
        except Exception as e:
            return tc, ToolResult(success=False, data={}, error=str(e))

    # ── Synchronous message processing (for tests) ──

    async def process_message(self, mail: Mail) -> None:
        """Process a single mail synchronously via inline step/collect loop.

        Used by tests. Creates a session and drives step_session /
        collect_session_results until completion.
        """
        session = AgentSession(
            session_id=f"{self._identity.id}-msg-{uuid.uuid4().hex[:8]}",
            trigger_content=mail.content,
        )

        try:
            while not session.is_completed:
                with self._services.session_span(session):
                    if session.state == SessionState.WAITING:
                        # Yield to let background tool tasks start executing
                        await asyncio.sleep(0)
                        await self.collect_session_results(session)
                        if session.has_pending_tools:
                            # Wait for all pending tasks to complete, then collect
                            pending = list(session.get_pending_tasks().values())
                            await asyncio.gather(*pending, return_exceptions=True)
                            await self.collect_session_results(session)
                    elif session.state == SessionState.ACTIVE:
                        await self.step_session(session, mail)
                    else:
                        break
        finally:
            await session.cancel_pending()
            await self._executor.mark_mail_read(mail)
