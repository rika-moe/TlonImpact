"""act stage: execute this round's tool calls — all of them.

One Python semantic unit spanning both halves of the step/collect
model: :func:`act_stage_step` declares (no-tool reminder, sendEmail
conflict check, the tool dispatch list) and :func:`act_stage_collect`
harvests. Both are pure transformations ``Session x Services ->
StageOutcome`` (C2): the stage declares *what* to dispatch; the engine
owns the concurrency (one background task per tool) and applies the
external effects (mails, memory save, on-complete actions, completion).
"""

import json
from typing import Any

from tlon.agents.session import AgentSession
from tlon.agents.stages.outcome import MailEffect, StageOutcome
from tlon.agents.stages.services import StageServices
from tlon.base.events.event import EventType
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.tools.base import ToolResult


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

    # Conflict detection
    send_calls = [tc for tc in response.tool_calls if tc.function_name == "sendEmail"]
    other_calls = [tc for tc in response.tool_calls if tc.function_name != "sendEmail"]
    has_complete = any(tc.arguments.get("is_complete") for tc in send_calls)

    if has_complete and other_calls:
        return StageOutcome(
            context_appends=[
                {
                    "role": "user",
                    "content": (
                        "冲突：当 sendEmail 的 is_complete 设为 true 时，"
                        "不得与其他工具同时调用。\n"
                        "请重新规划。"
                    ),
                }
            ],
            advance_iteration=True,
        )

    # Declare the dispatch; the engine runs the tools concurrently.
    return StageOutcome(tool_dispatches=list(response.tool_calls))


async def act_stage_collect(svc: StageServices, session: AgentSession) -> StageOutcome:
    """act stage, collect half: harvest completed background tools.

    Processes results, declares the sendEmail reply mails, and
    transitions state back to ACTIVE (continue thinking) or declares
    completion (done). Completion is event-driven by act
    (``is_complete``), not a loop boundary condition, so it stays
    outside the until expression.

    Session-state methods (add_tool_result / advance_iteration /
    mark_active) operate on the stage's own input state and are called
    directly; the externally visible effects are observability through
    services (TOOL_EXECUTE_END / TOOL_ERROR events) or declarations in
    the Outcome (reply mails, interaction save, on-complete actions,
    the completion signal — the engine runs them in this order:
    context appends -> mails -> save -> on-complete -> mark_completed).
    """
    completed = session.collect_completed()
    if not completed:
        return StageOutcome()  # still waiting

    outcome = StageOutcome()
    is_complete = False
    final_content = ""

    for tc, result in completed:
        session.add_tool_result(tc, result)

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

        if tc.function_name == "sendEmail" and result.success:
            content = result.data.get("content", "")
            trigger_mail = session.trigger_message
            # Reply semantics: an explicit `to` wins; otherwise the
            # reply goes back to the sender of the trigger mail, with
            # thread headers inherited from it.
            to = result.data.get("to") or ([trigger_mail.from_address] if trigger_mail else [])
            outcome.mails.append(
                MailEffect(
                    to=to,
                    content=content,
                    cc=result.data.get("cc"),
                    subject=result.data.get("subject"),
                    in_reply_to=trigger_mail.id if trigger_mail else None,
                    thread_id=trigger_mail.thread_id if trigger_mail else None,
                )
            )
            if result.data.get("is_complete"):
                is_complete = True
                final_content = content

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
        llm_resp = session.last_llm_response
        if llm_resp:
            outcome.save_interaction = final_content
        svc.logger.info("session_completed", session_id=session.id, content=final_content)
        # On-complete action expressions (phase 4b): declared here,
        # executed by the engine with full failure isolation — they
        # never affect the session lifecycle. The engine runs them
        # inside the session span, so the spawn quota can attribute
        # spawns to this session.
        outcome.run_on_complete = True
        outcome.complete = True
    elif not session.has_pending_tools:
        session.advance_iteration()
        session.mark_active()

    return outcome


def _no_tool_reminder_appends(response: LLMResponse) -> list[dict[str, Any]]:
    """Reminder messages for an LLM response without tool calls."""
    if response.content:
        msg: dict[str, Any] = {"role": "assistant", "content": response.content}
        if response.reasoning_content:
            msg["reasoning_content"] = response.reasoning_content
        return [
            msg,
            {
                "role": "user",
                "content": (
                    "注意：你必须调用至少一个工具，禁止直接输出文本。\n"
                    "请使用 sendEmail 工具发送邮件：\n"
                    "- 最终回复：sendEmail(to=['发件人地址'], content='你的回复', is_complete=true)\n"
                    "- 中间进度：sendEmail(to=['发件人地址'], content='进度报告', is_complete=false)，"
                    "然后继续调用其他工具。"
                ),
            },
        ]
    return [
        {
            "role": "user",
            "content": (
                "你必须调用至少一个工具。"
                "请使用 sendEmail 工具发送邮件，并设置 is_complete 参数：\n"
                "- is_complete=true：最终回复，结束本轮\n"
                "- is_complete=false：中间进度，继续思考"
            ),
        }
    ]


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
