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
expression evaluated pre-think. The engine owns the loop, including two
unconditional hard ceilings no ``until`` can override — ``max_iterations``
and the session token budget ``max_tokens`` (ADR 0023), whichever fires
first; stage run policy lives in the program (``:if``), not the engine.

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
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from functools import partial
from typing import Any

from tlon.agents.budget_notice import resource_limit_notice
from tlon.agents.context_pressure import ContextPressure
from tlon.agents.executor import EffectExecutor
from tlon.agents.member_spec import SessionRecipe
from tlon.agents.session import AgentSession, SessionState
from tlon.agents.session_journal import SessionJournal
from tlon.agents.stages.act import (
    act_stage_collect,
    act_stage_step,
    attempts_completion,
    attempts_mail_delivery,
)
from tlon.agents.stages.critique import critique_stage
from tlon.agents.stages.outcome import StageOutcome
from tlon.agents.stages.plan import plan_stage
from tlon.agents.stages.revise import revise_stage
from tlon.agents.stages.services import StageServices
from tlon.agents.stages.think import think_stage
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.trigger import AgentIdentity
from tlon.agents.vocabulary import (
    LOOP_BUILTINS,
    STAGE_COND_BUILTINS,
    loop_until_env,
    stage_cond_env,
)
from tlon.base.events.event import EventType
from tlon.base.llm_client import ContextWindowExceededError, LLMResponse, ToolCall
from tlon.base.logger import get_logger
from tlon.operations.core import CallContext
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.member_mail import MailDraft
from tlon.tools.base import ToolResult
from tlon.tools.spill import ToolSpill
from tlosure.builtins import EvalError
from tlosure.eval import DEFAULT_FUEL, eval_form

logger = get_logger(__name__)

#: User-facing fixed termination replies — the engine's built-in fallback
#: wording, used for any kind the member's recipe's ``:replies`` does not
#: override (replies are load-time-validated plain data, used verbatim).
#: Termination replies go to human senders only (ADR 0027, anchor revised
#: by ADR 0028 决策 7): every human sender in the session's
#: responsibility set gets one mail; agent senders are journaled + logged,
#: never answered.
_ITERATION_LIMIT_REPLY = "思考时间过长，请简化您的问题。"
_ERROR_REPLY = "抱歉，处理邮件时出现了错误。"
_TIMEOUT_REPLY = "抱歉，处理超时了，请稍后重试。"

#: The wake trigger text (ADR 0028 决策 2): fixed template, no mail body —
#: ``n`` is the wake-time count of wakeable unread mails, taken from the
#: same provider snapshot that becomes the session's responsibility set
#: (R5). Short and structurally stable, so the prefix cache hits
#: everything before it.
WAKE_TRIGGER_TEMPLATE = "你有 {n} 封未读邮件"

#: Engine grace added on top of a tool's own declared timeout. A tool that
#: declares a `timeout` parameter is its own timeout authority (e.g.
#: shell-run kills the command in-container at its declared
#: value); the engine's wait_for only guards against the tool itself
#: hanging past its deadline. Tools without one fall back to the engine's
#: per-tool limit (tool.per_timeout).
_TOOL_TIMEOUT_GRACE = 10.0


class LoopDriver:
    """Drives the ReAct step/collect cycle for agent sessions."""

    def __init__(
        self,
        identity: AgentIdentity,
        recipe: SessionRecipe,
        services: StageServices,
        executor: EffectExecutor,
        tool_executor: ToolExecutor,
        per_tool_timeout: float,
        journal: SessionJournal,
        spill: ToolSpill,
        pressure: ContextPressure,
        is_human_sender: Callable[[str], bool],
        prepare_scheduling_request: Callable[[str, dict[str, Any], str], Awaitable[None]]
        | None = None,
    ):
        self._prepare_scheduling_request = prepare_scheduling_request
        self._identity = identity
        self._loop = recipe
        # Phase D: the loop's self-declared :fuel caps the DSL evaluation of
        # its :until / stage conditions (None = engine default). The loop's
        # control flow (max_iterations, stage whitelist, until timing) is
        # untouched.
        self._loop_fuel = self._loop.fuel if self._loop.fuel is not None else DEFAULT_FUEL
        self._services = services
        self._executor = executor
        self._tool_executor = tool_executor
        self._per_tool_timeout = per_tool_timeout
        # Session event journal (ADR 0007): every state change is appended
        # before the in-memory mutation — the log is the truth.
        self._journal = journal
        # Oversized tool outputs spill to the workspace; the model (and the
        # journal) only ever see the locator (harness-review item 2).
        self._spill = spill
        # Context pressure relief (ADR 0008): checked before each step and
        # forced after a context-length 4xx.
        self._pressure = pressure
        # Termination replies go to human senders only (ADR 0027, anchor
        # revised by ADR 0028 决策 7): the predicate classifies each
        # responsibility-set sender. The caller resolves the human set on
        # each invocation — it grows at runtime.
        self._is_human_sender = is_human_sender
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

    async def step_session(self, session: AgentSession) -> None:
        """Advance a session by one step: until check + stage vector.

        The session's state transitions ACTIVE -> WAITING (tools running).
        When tools complete, collect_session_results transitions back to ACTIVE.
        """
        log = self._services.logger

        # Build context on first call (after LLM error: skip rebuild). The
        # session/start event is journaled before the context lands in
        # memory and before the first stage runs (ADR 0007); it carries
        # the responsibility-set id snapshot (ADR 0028 — crash recovery
        # reads it back for the chain-wake comparison).
        if not session.context:
            build_id = await self._journal.verify_root(session.id)
            built = await self._services.build_context(
                session.trigger_content, session.responsibility
            )
            await self._journal.append(
                session.id,
                "session/start",
                agent_id=self._identity.id,
                build_id=build_id,
                trigger_content=session.trigger_content,
                recipe=session.recipe_source,
                source_mail_id=session.source_mail_id,
                invocation=session.invocation,
                carrier=session.carrier,
                responsibility=[mail.id for mail in session.responsibility],
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

        # Engine hard ceilings (ADR 0023): max_iterations and max_tokens
        # are infrastructure caps, enforced no matter what the until
        # expression says — either one firing ends the session. Exhaustion
        # produces a deterministic notification based on actual tool results.
        # No additional LLM call may invent progress after the ceiling.
        # Replies go to the human senders of the responsibility set only
        # (ADR 0027, anchor revised by ADR 0028 决策 7).
        iteration_capped = session.iteration >= loop.max_iterations
        token_capped = session.token_usage >= loop.max_tokens
        if iteration_capped or token_capped:
            if token_capped:
                log.warning(
                    "session_max_tokens",
                    session_id=session.id,
                    token_usage=session.token_usage,
                    max_tokens=loop.max_tokens,
                )
            else:
                log.warning(
                    "session_max_iterations",
                    session_id=session.id,
                    max_iterations=loop.max_iterations,
                )
            reason = "max_tokens" if token_capped else "max_iterations"
            content = resource_limit_notice(
                session, reason, self._loop.replies.get("iteration_limit")
            )
            await self._send_termination_reply(session, reason, content)
            await self._journal.append(session.id, "session/end", reason=reason)
            session.mark_completed()
            return

        # until expression (phase 7a): evaluated pre-think only.
        if self._until_reached(session):
            await self._terminate_loop(session)
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
        if outcome.token_usage > 0:
            # Session token budget (ADR 0023): the journaled "tokens"
            # value is the post-accumulation absolute total, same pattern
            # as iteration/errors.
            await self._journal.append(
                session.id, "tokens", value=session.token_usage + outcome.token_usage
            )
            session.add_tokens(outcome.token_usage)
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
            build_id = await self._journal.verify_root(session.id)
            # Declared by act; the concurrency is the engine's: one
            # background task and one TOOL_EXECUTE_START event per tool.
            await self._journal.append(
                session.id,
                "tool/dispatch",
                tool_calls=[tc.to_dict() for tc in outcome.tool_dispatches],
                operation_recovery={
                    tc.id: self._operation_recovery(session.id, tc, session.iteration, build_id)
                    for tc in outcome.tool_dispatches
                },
            )
            tasks = {}
            for tc in outcome.tool_dispatches:
                tasks[tc.id] = asyncio.create_task(
                    self.execute_one_tool(
                        session.id, tc, self._operation_context(session.id, tc, session.iteration)
                    )
                )
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
        if outcome.complete:
            request = session.loop_scratch.get("scheduling_request")
            if request and self._prepare_scheduling_request is not None:
                await self._prepare_scheduling_request(self._identity.id, request, session.id)
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
        env = dict(
            loop_until_env(
                member_id=self._identity.id,
                name=self._identity.name,
                iteration=session.iteration,
                error_count=session.error_count,
                token_usage=session.token_usage,
                max_iterations=loop.max_iterations,
                max_consecutive_errors=loop.max_consecutive_errors,
                max_tokens=loop.max_tokens,
            )
        )
        try:
            result = eval_form(loop.until, env, LOOP_BUILTINS, fuel=self._loop_fuel)
        except EvalError as e:
            log.warning("loop_until_eval_failed", session_id=session.id, error=str(e))
            return (
                session.iteration >= loop.max_iterations
                or session.error_count >= loop.max_consecutive_errors
                or session.token_usage >= loop.max_tokens
            )
        reached = bool(result)
        log.debug("loop_until_evaluated", session_id=session.id, reached=reached)
        return reached

    def _termination_reply(self, kind: str, default: str) -> str:
        """The member's recipe's ``:replies`` override for ``kind``, or the
        engine's built-in fallback. Pure data lookup — the termination
        path must never fail, so no evaluation or templating happens here.
        """
        return self._loop.replies.get(kind, default)

    async def _send_termination_reply(
        self, session: AgentSession, reason: str, content: str
    ) -> None:
        """Send termination notifications — to the responsibility set's
        human senders only (ADR 0027, anchor revised by ADR 0028 决策 7).

        The trigger mail no longer exists; the anchor is the session's
        responsibility set: each *human* sender gets exactly one mail
        (deduplicated by address), its ``in_reply_to`` pointing at that
        sender's latest mail in the set. A non-human sender (agent,
        postmaster, external address) gets no mail — the termination
        stays journaled and is logged here, breaking the failure ->
        reply -> retrigger oscillation between agents.
        """
        # Snapshot order is chronological, so the last write per sender
        # is that sender's latest responsibility mail.
        latest_by_sender: dict[str, Mail] = {}
        for mail in session.responsibility:
            latest_by_sender[mail.from_address] = mail
        notified: list[str] = []
        for sender, mail in latest_by_sender.items():
            if not await asyncio.to_thread(self._is_human_sender, sender):
                self._services.logger.info(
                    "termination_reply_suppressed",
                    session_id=session.id,
                    sender=sender,
                    reason=reason,
                )
                continue
            try:
                await self._executor.send_mail(
                    MailDraft(
                        to=[sender],
                        content=content,
                        subject="运行时通知：处理达到资源上限"
                        if reason in ("max_tokens", "max_iterations")
                        else None,
                        in_reply_to=mail.id,
                        thread_id=mail.thread_id,
                    ),
                    request_id=f"{session.id}:termination:{reason}:{sender}",
                )
            except Exception:
                self._services.logger.error("termination_reply_failed", exc_info=True)
            else:
                notified.append(sender)
        if notified:
            self._services.logger.info(
                "termination_replies_sent",
                session_id=session.id,
                reason=reason,
                recipients=notified,
            )

    async def _terminate_loop(self, session: AgentSession) -> None:
        """Terminate a session whose until expression fired.

        The finishing action is chosen by state: error limit -> the error
        mail; anything else -> the iteration-limit mail. The reply goes to
        the responsibility set's human senders only (ADR 0027/0028); the
        journal is written either way.
        """
        log = self._services.logger
        loop = self._loop
        if session.error_count >= loop.max_consecutive_errors:
            reason = "error_limit"
            content = self._termination_reply("error", _ERROR_REPLY)
            log.warning(
                "session_max_errors",
                session_id=session.id,
                error_count=session.error_count,
                max_errors=loop.max_consecutive_errors,
            )
        else:
            reason = "iteration_limit"
            content = self._termination_reply("iteration_limit", _ITERATION_LIMIT_REPLY)
        log.info("loop_until_reached", session_id=session.id, reason=reason)
        await self._send_termination_reply(session, reason, content)
        await self._journal.append(session.id, "session/end", reason="until", detail=reason)
        session.mark_completed()

    async def send_failure_reply(self, session: AgentSession, reason: str) -> None:
        """Notify the responsibility set's human senders when the scheduler
        ends a session abnormally (idle timeout / unhandled stage error).

        Human senders only (ADR 0027, anchor revised by ADR 0028 决策 7):
        agent senders end with a log line, no mail. The fatal paths live
        in the scheduler; the reply goes through the same MailDraft ->
        EffectExecutor pipeline every other engine mail uses (send_mail
        never raises).
        """
        content = (
            self._termination_reply("timeout", _TIMEOUT_REPLY)
            if reason == "timeout"
            else self._termination_reply("error", _ERROR_REPLY)
        )
        await self._send_termination_reply(session, reason, content)

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
        env = dict(
            stage_cond_env(
                iteration=session.iteration,
                error_count=session.error_count,
                token_usage=session.token_usage,
                scratch=session.loop_scratch,
                completing=attempts_completion(response),
                sending_mail=attempts_mail_delivery(response),
            )
        )
        try:
            result = eval_form(condition, env, STAGE_COND_BUILTINS, fuel=self._loop_fuel)
        except EvalError as e:
            log.warning(
                "loop_stage_condition_failed",
                session_id=session.id,
                stage=name,
                error=str(e),
            )
            return False
        if not result:
            log.debug("loop_stage_skipped", session_id=session.id, stage=name, reason="condition")
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

    async def execute_one_tool(
        self,
        session_id: str,
        tc: ToolCall,
        context: CallContext | None = None,
        *,
        journal_result: bool = True,
    ) -> tuple[ToolCall, ToolResult]:
        """Execute one tool with timeout, returning (tc, result) tuple.

        Catches all exceptions and returns ToolResult(success=False).
        Designed to run via asyncio.create_task in step_session. The
        result is journaled (``tool/result``) as soon as it exists — the
        log must have it even if the process dies before the collect.
        """
        context = context or self._operation_context(session_id, tc, 0)
        timeout, authority = self._tool_timeout_budget(tc)
        result: ToolResult
        try:
            result = await asyncio.wait_for(
                self._tool_executor.execute(tc.function_name, tc.arguments, context),
                timeout=timeout,
            )
        except TimeoutError:
            tool = self._tool_executor.get_tool(tc.function_name)
            result = tool.timeout_result(tc.arguments, context)
            result.error = f"Timeout after {timeout}s ({authority}); {result.error}"
        except Exception as e:
            result = ToolResult(success=False, data={}, error=str(e))
        # Spill before journaling: log and context hold the same locator.
        spill_call = replace(tc, id=hashlib.sha256(context.call_id.encode()).hexdigest())
        result = await self._spill.maybe_spill(session_id, spill_call, result)
        if journal_result:
            await self._journal.append(
                session_id, "tool/result", tool_call=tc.to_dict(), result=result.to_dict()
            )
        return tc, result

    def _operation_context(self, session_id: str, tc: ToolCall, iteration: int) -> CallContext:
        return CallContext(
            self._identity.id,
            session_id,
            f"session:{session_id}:round:{iteration}:{tc.id}",
            call_path=(f"round:{iteration}", f"tool:{tc.id}"),
            origin="tool",
        )

    def _operation_recovery(
        self, session_id: str, tc: ToolCall, iteration: int, build_id: str | None
    ) -> dict[str, Any]:
        context = self._operation_context(session_id, tc, iteration)
        tool = (
            self._tool_executor.get_tool(tc.function_name)
            if self._tool_executor.has_tool(tc.function_name)
            else None
        )
        return {
            "build_id": build_id,
            "root_id": session_id,
            "call_id": context.call_id,
            "call_path": list(context.call_path),
            "definition_id": tool.definition_id if tool is not None else None,
        }

    async def resolve_dispatch(
        self, session_id: str, tc: ToolCall, metadata: dict[str, Any]
    ) -> ToolResult:
        """Resolve frozen dispatch through the shared operation authority."""
        required = {"root_id", "call_id", "call_path", "build_id", "definition_id"}
        if (
            not required.issubset(metadata)
            or metadata["root_id"] != session_id
            or not metadata["call_id"]
            or not isinstance(metadata["call_path"], list)
            or not metadata["call_path"]
        ):
            raise ValueError("Dispatch lacks frozen invocation identity")
        build_id = await self._journal.verify_root(session_id)
        if metadata.get("build_id") != build_id:
            raise ValueError("Dispatch runtime build changed")
        tool = (
            self._tool_executor.get_tool(tc.function_name)
            if self._tool_executor.has_tool(tc.function_name)
            else None
        )
        definition_id = tool.definition_id if tool is not None else None
        if metadata["definition_id"] != definition_id:
            raise ValueError("Dispatch operation definition changed")
        context = CallContext(
            self._identity.id,
            session_id,
            metadata["call_id"],
            call_path=tuple(metadata["call_path"]),
            origin="tool",
        )
        _, result = await self.execute_one_tool(session_id, tc, context, journal_result=False)
        return result

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
            declared = tc.arguments.get(
                "timeout", schema.get("default", getattr(tool, "default_timeout", None))
            )
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

        Used by tests. Creates a session (responsibility = this one mail,
        wake-template trigger text) and drives step_session /
        collect_session_results until completion. Read-marking is the
        agent's own act under ADR 0028 — nothing is marked here.
        """
        session = AgentSession(
            session_id=f"{self._identity.id}-msg-{uuid.uuid4().hex[:8]}",
            trigger_content=WAKE_TRIGGER_TEMPLATE.format(n=1),
            responsibility=[mail],
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
                        await self.step_session(session)
                    else:
                        break
        finally:
            await session.cancel_pending()
