"""critique stage (phase 7c): review the round's latest output."""

import json
from typing import Any

from tlon.agents.session import AgentSession
from tlon.agents.stages.outcome import StageOutcome
from tlon.agents.stages.services import StageServices
from tlon.base.llm_client import ContextWindowExceededError, LLMResponse


async def critique_stage(
    svc: StageServices,
    session: AgentSession,
    response: "LLMResponse | None",
    _opts: dict[str, Any] | None = None,
) -> StageOutcome:
    """critique stage (phase 7c): review the round's latest output.

    The review input is the session context plus, when the stage runs
    before ``act`` (the pre-submission gate), the round's pending
    response rendered as the latest assistant message — the draft under
    review has not entered the context yet, and a reviewer that cannot
    see it could never confirm a revision (a FAIL would loop to the
    iteration ceiling). The render is input-only: the session's context
    is untouched (declared effects only).

    Parses the verdict's first line: PASS -> passed; FAIL -> failed
    with the remaining text as feedback; an unparseable first line is
    "we don't know the review outcome" — treating it as PASS would be
    fail-open, so the critique is skipped like a soft failure (warning
    logged, no verdict written: neither critique-passed? nor
    critique-failed? fires, revise is not triggered). The verdict is
    declared for the loop scratchpad. Fails soft like plan; does not
    consume or produce the round's response.
    """
    messages = list(session.context)
    if response is not None:
        draft = _render_draft(response)
        if draft:
            messages.append({"role": "assistant", "content": draft})
    messages.append(
        {
            "role": "user",
            "content": (
                "请评审上述最新输出是否充分回应了当前邮件。"
                "第一行只写 PASS 或 FAIL；"
                "FAIL 时从第二行起给出具体改进意见。"
            ),
        }
    )
    try:
        verdict_response = await svc.think(messages, span="agent.critique")
    except ContextWindowExceededError:
        # ADR 0008: not a soft failure — the engine force-compacts and
        # retries this stage once.
        raise
    except Exception as e:
        svc.logger.warning(
            "loop_stage_failed", session_id=session.id, stage="critique", error=str(e)
        )
        return StageOutcome(response=response)
    text = (verdict_response.content or "").strip()
    first_line, _, rest = text.partition("\n")
    verdict = first_line.strip()
    if verdict.startswith("PASS"):
        passed, feedback = True, ""
    elif verdict.startswith("FAIL"):
        passed, feedback = False, rest.strip()
    else:
        # Unparseable verdict: an unknown review outcome must not be
        # recorded as PASS (fail-open). Skip this critique like the
        # soft-failure path above — no verdict in scratch, so neither
        # critique-passed? nor critique-failed? fires and revise is not
        # triggered — but leave a visible warning.
        svc.logger.warning(
            "critique_verdict_unparseable",
            session_id=session.id,
            stage="critique",
            raw=text[:200],
        )
        return StageOutcome(response=response)
    return StageOutcome(
        response=response,
        scratch_updates={"critique": {"passed": passed, "feedback": feedback}},
    )


def _render_draft(response: LLMResponse) -> str:
    """Render the round's pending response as reviewable text.

    The reply text lives in the ``sendEmail`` arguments for completing
    rounds, so tool calls are rendered alongside any direct content.
    """
    parts: list[str] = []
    if response.content:
        parts.append(response.content)
    for tc in response.tool_calls or []:
        parts.append(
            f"[调用工具] {tc.function_name}({json.dumps(tc.arguments, ensure_ascii=False)})"
        )
    return "\n".join(parts)
