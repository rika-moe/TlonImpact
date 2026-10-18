"""act stage: execute this round's tool calls — all of them.

One Python semantic unit spanning both halves of the step/collect
model: :func:`act_stage_step` declares (no-tool reminder, sendEmail
conflict check, the tool dispatch list) and :func:`act_stage_collect`
harvests. Both are pure transformations ``Session x Services ->
StageOutcome`` (C2): the stage declares *what* to dispatch and what the
harvested results imply; the engine owns the concurrency (one
background task per tool), the harvest (``session.collect_completed``),
all session-field writes, and the external effects (mails, on-complete
actions, completion).
"""

import json
from typing import Any

from tlon.agents.session import AgentSession
from tlon.agents.stages.outcome import StageOutcome
from tlon.agents.stages.services import StageServices
from tlon.base.events.event import EventType
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.tools.base import ToolResult

ENDING_TOOLS = frozenset({"finishTurn", "sleepUntil", "exitAgent"})


def attempts_completion(response: LLMResponse | None) -> bool:
    """Ending requests are dispatched only when they are the sole tool call."""
    return bool(
        response
        and response.tool_calls
        and len(response.tool_calls) == 1
        and response.tool_calls[0].function_name in ENDING_TOOLS
    )


def attempts_mail_delivery(response: LLMResponse | None) -> bool:
    """Whether act would dispatch mail delivery in this response."""
    return bool(
        response
        and response.tool_calls
        and not any(tc.function_name in ENDING_TOOLS for tc in response.tool_calls)
        and any(tc.function_name in {"sendEmail", "forwardMail"} for tc in response.tool_calls)
    )


async def act_stage_step(
    svc: StageServices,
    session: AgentSession,
    response: "LLMResponse | None",
    _opts: dict[str, Any] | None = None,
) -> StageOutcome:
    """act stage, step half: declare this round's tool execution.

    Covers the no-tool reminder, the sendEmail conflict check, and the
    dispatch declaration; the harvest half is act_stage_collect. The
    declared ``tool_dispatches`` are executed by the engine: one
    TOOL_EXECUTE_START event and one background task per tool call,
    then ``session.dispatch_tools`` + ``mark_waiting``.
    """
    if response is None:
        svc.logger.debug(
            "loop_stage_skipped", session_id=session.id, stage="act", reason="no_response"
        )
        return StageOutcome()

    # No tool calls
    if not response.tool_calls:
        return StageOutcome(
            context_appends=_no_tool_reminder_appends(response), advance_iteration=True
        )

    if (
        any(tc.function_name in ENDING_TOOLS for tc in response.tool_calls)
        and len(response.tool_calls) != 1
    ):
        return StageOutcome(
            context_appends=[
                {
                    "role": "user",
                    "content": "冲突：finishTurn、sleepUntil、exitAgent 必须单独调用，不能与其他工具同时调用。请另起一轮。",
                }
            ],
            advance_iteration=True,
        )

    # Declare the dispatch; the engine runs the tools concurrently.
    return StageOutcome(tool_dispatches=list(response.tool_calls))


async def act_stage_collect(
    svc: StageServices,
    session: AgentSession,
    completed: list[tuple[ToolCall, ToolResult]],
) -> StageOutcome:
    """act stage, collect half: process harvested background tools.

    ``completed`` is harvested by the engine
    (``session.collect_completed``); the stage only reads session state
    and declares everything it wants changed: the tool results to
    record, the sendEmail reply mails, the context appends, the
    iteration advance / ACTIVE transition (continue thinking), or the
    final reply + on-complete actions + completion signal (done).
    Completion is event-driven by explicit scheduling tools, not a loop
    boundary condition, so it stays outside the until expression.

    The engine applies the Outcome in this order: context appends ->
    counters/state -> tool results -> mails -> on-complete ->
    mark_completed.
    """
    if not completed:
        return StageOutcome()  # still waiting

    outcome = StageOutcome()
    is_complete = False

    for tc, result in completed:
        outcome.tool_results.append((tc, result))

        await svc.publish_event(
            EventType.TOOL_EXECUTE_END,
            {
                "agent_id": svc.identity.id,
                "tool_name": tc.function_name,
                "success": result.success,
            },
        )
        if not result.success and result.error:
            await svc.publish_event(
                EventType.TOOL_ERROR,
                {
                    "agent_id": svc.identity.id,
                    "tool_name": tc.function_name,
                    "error": result.error,
                },
            )

        if tc.function_name in ENDING_TOOLS and result.success:
            request = result.data.get("scheduling_request")
            if request:
                is_complete = True
                outcome.scratch_updates["scheduling_request"] = request

        # Append to context
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [_tool_call_to_dict(tc)],
        }
        llm_resp = session.last_llm_response
        if llm_resp and llm_resp.reasoning_content:
            assistant_msg["reasoning_content"] = llm_resp.reasoning_content
        outcome.context_appends.append(assistant_msg)
        outcome.context_appends.append(
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _tool_result_to_string(result),
            }
        )

    if is_complete:
        svc.logger.info("session_completed", session_id=session.id)
        # On-complete action expressions (phase 4b): declared here,
        # executed by the engine with full failure isolation — they
        # never affect the session lifecycle. The engine runs them
        # inside the session span, so the spawn quota can attribute
        # spawns to this session.
        outcome.complete = True
    elif not session.has_pending_tools:
        outcome.advance_iteration = True
        outcome.mark_active = True

    return outcome


def _no_tool_reminder_appends(response: LLMResponse) -> list[dict[str, Any]]:
    """Require explicit actions and keep mail delivery separate from scheduling."""
    appends: list[dict[str, Any]] = []
    if response.content:
        msg: dict[str, Any] = {"role": "assistant", "content": response.content}
        if response.reasoning_content:
            msg["reasoning_content"] = response.reasoning_content
        appends.append(msg)
    appends.append(
        {
            "role": "user",
            "content": (
                "你必须调用工具，不能直接输出文本。需要发信时调用 sendEmail 或 forwardMail；"
                "发信不会结束工作。完成后另起一轮单独调用 finishTurn，"
                "或使用 sleepUntil 定时睡眠、exitAgent 永久退出。"
            ),
        }
    )
    return appends


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


def _tool_result_to_string(result: ToolResult) -> str:
    """Convert tool result to string for context."""
    if hasattr(result, "to_dict"):
        return json.dumps(result.to_dict())
    return str(result)
