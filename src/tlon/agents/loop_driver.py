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

from tlon.agents.builtins import LOOP_BUILTINS, STAGE_COND_BUILTINS, EvalError
from tlon.agents.context_pressure import ContextPressure, estimate_tokens
from tlon.agents.eval import DEFAULT_FUEL, eval_form
from tlon.agents.executor import EffectExecutor
from tlon.agents.member_spec import MemberLoop, MemberRule
from tlon.agents.on_complete import OnCompleteRunner
from tlon.agents.session import AgentSession, SessionState
from tlon.agents.session_journal import SessionJournal
from tlon.agents.stages.act import act_stage_collect, act_stage_step, attempts_completion
from tlon.agents.stages.critique import critique_stage
from tlon.agents.stages.outcome import MailEffect, StageOutcome
from tlon.agents.stages.plan import plan_stage
from tlon.agents.stages.revise import revise_stage
from tlon.agents.stages.services import StageServices
from tlon.agents.stages.think import think_stage
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.trigger import AgentIdentity
from tlon.base.events.event import EventType
from tlon.base.llm_client import ContextWindowExceededError, LLMResponse, ToolCall
from tlon.base.logger import get_logger
from tlon.organization.mail.mail import Mail
from tlon.tools.base import ToolResult
from tlon.tools.spill import ToolSpill

logger = get_logger(__name__)

#: Fallback reply when max_iterations is hit and the distillation call
#: itself fails — the termination path must never fail.
_MAX_ITERATIONS_FALLBACK = "思考时间过长，请简化您的问题。"

#: Distillation prompt for the max-iterations reply: the engine's hard
#: ceiling fired mid-work, so one extra LLM call condenses the session's
#: intermediate results into the reply instead of a bare apology.
_MAX_ITERATIONS_SYSTEM_PROMPT = (
    "你是通过邮件工作的智能体。你处理一封来信时达到了内部迭代上限，任务未能彻底完成。"
    "请根据给出的工作记录，给来信人写一封简短的中文回信：说明已经做了什么、"
    "进展到哪一步、为什么没能完成。语气诚恳简洁，只输出回信正文。"
)

#: Token budget (estimate_tokens units) for the distillation input tail:
#: a session that hit the ceiling may sit near the context-window cap,
#: so the input is the key tail, never the full context.
_DISTILL_TOKEN_BUDGET = 40_000

#: Engine grace added on top of a tool's own declared timeout. A tool that
#: declares a `timeout` parameter is its own timeout authority (e.g.
#: executeBashCommand kills the command in-container at its declared
#: value); the engine's wait_for only guards against the tool itself
#: hanging past its deadline. Tools without one fall back to the engine's
#: per-tool limit (tool.per_timeout).
_TOOL_TIMEOUT_GRACE = 10.0

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
        journal: SessionJournal,
        spill: ToolSpill,
        pressure: ContextPressure,
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
        # Session event journal (ADR 0007): every state change is appended
        # before the in-memory mutation — the log is the truth.
        self._journal = journal
        # Oversized tool outputs spill to the workspace; the model (and the
        # journal) only ever see the locator (harness-review item 2).
        self._spill = spill
        # Context pressure relief (ADR 0008): checked before each step and
        # forced after a context-length 4xx.
        self._pressure = pressure
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

        # The trigger mail is recorded on the first step, unconditionally —
        # a resumed session already has context but still needs the mail
        # (on-complete action expressions read it as ``msg``).
        if session.trigger_message is None:
            session.trigger_message = mail

        # Build context on first call (after LLM error: skip rebuild). The
        # session/start event is journaled before the context lands in
        # memory and before the first stage runs (ADR 0007).
        if not session.context:
            built = await self._services.build_context(mail)
            await self._journal.append(
                session.id,
                "session/start",
                agent_id=self._identity.id,
                mail_id=mail.id,
                trigger_content=session.trigger_content,
                carrier=session.carrier,
                context=built.messages,
                anchor_len=built.anchor_len,
            )
            session.context = built.messages
            # The initial build block minus the trailing knowledge message
            # is the compaction anchor (ADR 0008, revised by ADR 0017) —
            # never compressed, never dropped. Knowledge sits after the
            # anchor as ordinary compressible middle.
            session.anchor_len = built.anchor_len
            log.debug(
                "session_context_built",
                session_id=session.id,
                message_count=len(built.messages),
            )

        # Context pressure check (ADR 0008): prune/summarize the
        # model-facing view before this step's LLM calls when the estimated
        # size crosses the threshold. Journaled before mutating (ADR 0007).
        await self._pressure.maybe_compact(session)

        loop = self._loop

        # Engine hard ceiling: max_iterations is an infrastructure cap,
        # enforced no matter what the until expression says. Exhaustion
        # is not failure — the session's intermediate work is distilled
        # into the reply by one extra LLM call (fixed-text fallback).
        if session.iteration >= loop.max_iterations:
            log.warning(
                "session_max_iterations",
                session_id=session.id,
                max_iterations=loop.max_iterations,
            )
            await self._executor.send_mail(
                MailEffect(
                    to=[mail.from_address],
                    content=await self._distill_reply(session),
                    in_reply_to=mail.id,
                    thread_id=mail.thread_id,
                )
            )
            await self._journal.append(session.id, "session/end", reason="max_iterations")
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
            if not self._stage_condition_met(session, name, opts, response):
                continue
            log.debug("loop_stage_start", session_id=session.id, stage=name)
            try:
                outcome = await handler(session, response, opts)
            except ContextWindowExceededError:
                # Emergency path (ADR 0008): the stage's LLM call was
                # rejected for context length. Stages re-raise this error
                # instead of folding it into their record_error fallback so
                # the engine can act on it: force one full compaction
                # (tightened keep window) and retry the stage exactly once.
                # A second failure is NOT caught here — it propagates to
                # the scheduler's session_error handler, which journals
                # session/end (the pre-existing fatal path for a stage that
                # cannot produce an outcome at all).
                log.warning(
                    "loop_stage_context_window_exceeded",
                    session_id=session.id,
                    stage=name,
                )
                await self._pressure.maybe_compact(session, force=True)
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
            await self._journal.append(session.id, "iteration", value=session.iteration + 1)
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
        on-complete actions -> completion —
        equivalent to the pre-C2 interleaving (on-complete ->
        mark_completed stays inside the session
        span, so spawn quota attribution is unchanged).

        Event sourcing (ADR 0007): every session-state change is appended
        to the journal *before* the in-memory mutation; a failed append
        raises, leaving the session untouched (model-visible ⇒ journaled).
        Tool results are not journaled here — the collect path already
        covered them with tool/result + tool/collect.
        """
        if outcome.context_appends:
            await self._journal.append(
                session.id, "context/append", messages=outcome.context_appends
            )
            session.context.extend(outcome.context_appends)
        if outcome.scratch_updates:
            await self._journal.append(
                session.id, "scratch/update", updates=outcome.scratch_updates
            )
            session.loop_scratch.update(outcome.scratch_updates)
        if outcome.record_error:
            await self._journal.append(session.id, "errors", value=session.error_count + 1)
            session.record_error()
        if outcome.clear_errors and session.error_count > 0:
            # A successful think ends the error streak: the counter is
            # max_consecutive_errors, not a lifetime total. The journaled
            # "errors" value is absolute, so replay restores the reset.
            await self._journal.append(session.id, "errors", value=0)
            session.reset_errors()
        if outcome.advance_iteration:
            await self._journal.append(session.id, "iteration", value=session.iteration + 1)
            session.advance_iteration()
        if outcome.mark_active:
            await self._journal.append(session.id, "state", value=SessionState.ACTIVE.value)
            session.mark_active()
        for tc, result in outcome.tool_results:
            session.add_tool_result(tc, result)
        if outcome.response is not None:
            await self._journal.append(
                session.id, "llm/response", response=outcome.response.to_dict()
            )
            session.set_last_llm_response(outcome.response)
            response = outcome.response
        if outcome.tool_dispatches:
            # Declared by act; the concurrency is the engine's: one
            # background task and one TOOL_EXECUTE_START event per tool.
            await self._journal.append(
                session.id,
                "tool/dispatch",
                tool_calls=[tc.to_dict() for tc in outcome.tool_dispatches],
            )
            tasks = {}
            for tc in outcome.tool_dispatches:
                tasks[tc.id] = asyncio.create_task(self.execute_one_tool(session.id, tc))
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
            await self._journal.append(session.id, "state", value=SessionState.WAITING.value)
            session.mark_waiting()
        for effect in outcome.mails:
            await self._executor.send_mail(effect)
        if outcome.run_on_complete:
            await self._on_complete.run(session, outcome.final_reply or "")
        if outcome.complete:
            await self._journal.append(session.id, "session/end", reason="complete")
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
        await self._journal.append(session.id, "session/end", reason="until", detail=reason)
        session.mark_completed()

    async def _distill_reply(self, session: AgentSession) -> str:
        """Distill the session's intermediate work into the max-iterations reply.

        One extra LLM call over the context tail turns the completed
        rounds (thoughts, tool results) into a reply saying what was
        done, how far it got, and why it stopped. Fully defensive: any
        failure (LLM error, timeout, empty answer) falls back to the
        fixed text — the termination path itself must never fail.
        """
        log = self._services.logger
        try:
            response = await self._services.distill(
                _MAX_ITERATIONS_SYSTEM_PROMPT,
                self._distill_messages(session),
                span="agent.distill",
                session_id=session.id,
            )
            content = (response.content or "").strip()
            if not content:
                raise ValueError("empty distillation")
            return content
        except Exception as e:
            log.warning(
                "max_iterations_distill_failed", session_id=session.id, error=str(e)
            )
            return _MAX_ITERATIONS_FALLBACK

    @staticmethod
    def _distill_messages(session: AgentSession) -> list[dict[str, Any]]:
        """Build the distillation input: context tail under a token budget.

        The session may sit near the context-window cap after hitting the
        ceiling, so the input is the key tail (most recent rounds, where
        the progress lives) rather than the full context. The instruction
        carries the original request, which the tail may no longer reach.
        """
        tail: list[dict[str, Any]] = []
        tokens = 0
        for message in reversed(session.context):
            cost = estimate_tokens([message])
            if tail and tokens + cost > _DISTILL_TOKEN_BUDGET:
                break
            tail.append(message)
            tokens += cost
        tail.reverse()
        instruction = (
            "以上是你处理来信时的工作记录（可能不完整）。原始来信内容：\n\n"
            f"{session.trigger_content}\n\n请写回信正文。"
        )
        return [*tail, {"role": "user", "content": instruction}]

    async def send_failure_reply(self, mail: Mail, reason: str) -> None:
        """Reply to the trigger mail's sender when the scheduler ends a
        session abnormally (idle timeout / unhandled stage error).

        The fatal paths live in the scheduler; the reply goes through the
        same MailEffect -> EffectExecutor pipeline every other engine mail
        uses (send_mail never raises).
        """
        if reason == "timeout":
            content = "抱歉，处理这封邮件超时了，请稍后重试。"
        else:
            content = "抱歉，处理邮件时出现了错误。"
        await self._executor.send_mail(
            MailEffect(
                to=[mail.from_address],
                content=content,
                in_reply_to=mail.id,
                thread_id=mail.thread_id,
            )
        )

    def _stage_condition_met(
        self,
        session: AgentSession,
        name: str,
        opts: dict[str, Any],
        response: LLMResponse | None,
    ) -> bool:
        """Evaluate a stage's optional ``:if`` condition (phase 7c).

        The condition is a pure predicate over loop state, the session's
        loop scratchpad, and the round section — ``round.completing``
        reflects whether the response currently on the chain would end
        the session (act's completion rule). False skips the stage; an
        evaluation failure also skips it (safe default: never run a
        stage whose gating condition is broken).
        """
        condition = opts.get("if")
        if condition is None:
            return True
        log = self._services.logger
        env = {
            "state": {"iteration": session.iteration, "error_count": session.error_count},
            "scratch": session.loop_scratch,
            "round": {"completing": attempts_completion(response)},
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
        Outcome the engine applies here. The harvest is journaled as
        ``tool/collect`` before the stage runs (ADR 0007).
        """
        completed = session.collect_completed()
        if completed:
            await self._journal.append(
                session.id, "tool/collect", tool_call_ids=[tc.id for tc, _ in completed]
            )
        outcome = await act_stage_collect(self._services, session, completed)
        await self._apply_outcome(session, None, outcome)

    async def resume_collect(
        self, session: AgentSession, completed: list[tuple[ToolCall, ToolResult]]
    ) -> None:
        """Feed journaled-but-uncollected tool results into the collect half.

        Crash recovery (ADR 0007): a resumed session's orphan tool results
        (real ones journaled pre-crash plus synthetic interrupted ones)
        are collected exactly as if they had just finished — the
        ``tool/collect`` event is journaled so the log stays a complete
        dispatch -> result -> collect chain.
        """
        if completed:
            await self._journal.append(
                session.id, "tool/collect", tool_call_ids=[tc.id for tc, _ in completed]
            )
        outcome = await act_stage_collect(self._services, session, completed)
        await self._apply_outcome(session, None, outcome)

    async def execute_one_tool(self, session_id: str, tc: ToolCall) -> tuple[ToolCall, ToolResult]:
        """Execute one tool with timeout, returning (tc, result) tuple.

        Catches all exceptions and returns ToolResult(success=False).
        Designed to run via asyncio.create_task in step_session. The
        result is journaled (``tool/result``) as soon as it exists — the
        log must have it even if the process dies before the collect.
        """
        timeout, authority = self._tool_timeout_budget(tc)
        result: ToolResult
        try:
            result = await asyncio.wait_for(
                self._tool_executor.execute(tc.function_name, tc.arguments),
                timeout=timeout,
            )
        except TimeoutError:
            result = ToolResult(
                success=False,
                data={},
                error=f"Timeout after {timeout}s ({authority})",
            )
        except Exception as e:
            result = ToolResult(success=False, data={}, error=str(e))
        # Spill before journaling: log and context hold the same locator.
        result = await self._spill.maybe_spill(session_id, tc, result)
        await self._journal.append(
            session_id, "tool/result", tool_call=tc.to_dict(), result=result.to_dict()
        )
        return tc, result

    def _tool_timeout_budget(self, tc: ToolCall) -> tuple[float, str]:
        """Effective wait_for budget for one tool call, plus its authority.

        The schema the model sees must be honored: a tool declaring a
        ``timeout`` parameter self-limits at that value, so the engine
        waits the declared value (argument or schema default) plus
        ``_TOOL_TIMEOUT_GRACE``. Tools without one keep the engine's
        per-tool fallback (``tool.per_timeout``).
        """
        try:
            tool = self._tool_executor.get_tool(tc.function_name)
            schema = tool.parameters.get("properties", {}).get("timeout")
        except (AttributeError, ValueError):
            schema = None
        if isinstance(schema, dict):
            declared = tc.arguments.get("timeout", schema.get("default"))
            if isinstance(declared, (int, float)) and not isinstance(declared, bool):
                return float(declared) + _TOOL_TIMEOUT_GRACE, (
                    f"tool '{tc.function_name}' declared timeout {declared}s "
                    f"+ {_TOOL_TIMEOUT_GRACE}s engine grace"
                )
        return self._per_tool_timeout, (
            f"engine per-tool limit tool.per_timeout; "
            f"'{tc.function_name}' declares no timeout of its own"
        )

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
