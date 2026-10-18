"""critique stage (phase 7c): review the round's latest output."""

from typing import Any

from tlon.agents.session import AgentSession
from tlon.agents.stages.draft import render_review_output
from tlon.agents.stages.outcome import StageOutcome
from tlon.agents.stages.services import StageServices
from tlon.base.llm_client import ContextWindowExceededError, LLMResponse, usage_total_tokens


async def critique_stage(
    svc: StageServices,
    session: AgentSession,
    response: "LLMResponse | None",
    opts: dict[str, Any] | None = None,
) -> StageOutcome:
    """critique stage (phase 7c): review the round's latest output.

    The review input is the session context plus, when the stage runs
    before ``act`` (the pre-submission gate), the round's pending
    response rendered as plain text with explicit execution status — the draft under
    review has not entered the context yet, and a reviewer that cannot
    see it could never confirm a revision (a FAIL would loop to the
    iteration ceiling). The render is input-only: the session's context
    is untouched (declared effects only). Post-act reviews preserve collected
    receipts or the dispatched/unknown status rather than relabeling them as drafts.

    Parses the verdict's first line: PASS -> passed; FAIL -> failed
    with the remaining text as feedback; an unparseable first line is
    "we don't know the review outcome" — treating it as PASS would be
    fail-open, so the critique is skipped like a soft failure (warning
    logged, any previous verdict cleared: neither critique-passed? nor
    critique-failed? fires, revise is not triggered). The verdict is
    declared for the loop scratchpad. Fails soft like plan; does not
    consume or produce the round's response.
    """
    messages = list(session.context)
    if response is not None:
        draft = render_review_output(session, response)
        if draft:
            messages.append({"role": "assistant", "content": draft})
    instruction = (
        "请评审上述最新输出是否充分回应了当前邮件。"
        "调用状态以运行时标记和真实工具回执为准；尚未执行的草稿不是已发邮件或历史事实。"
    )
    if extra_instructions := (opts or {}).get("instructions"):
        instruction += f"\n额外评审标准：\n{extra_instructions}\n"
    instruction += "第一行只写 PASS 或 FAIL；FAIL 时从第二行起给出具体改进意见。"
    messages.append(
        {
            "role": "user",
            "content": instruction,
        }
    )
    try:
        verdict_response = await svc.think(
            messages,
            span="agent.critique",
            purpose="critique",
            session_id=session.id,
            cache_prefix_len=session.anchor_len,
        )
    except ContextWindowExceededError:
        # ADR 0008: not a soft failure — the engine force-compacts and
        # retries this stage once.
        raise
    except Exception as e:
        svc.logger.warning(
            "loop_stage_failed", session_id=session.id, stage="critique", error=str(e)
        )
        return StageOutcome(response=response, scratch_updates={"critique": None})
    text = (verdict_response.content or "").strip()
    first_line, _, rest = text.partition("\n")
    verdict = first_line.strip()
    if verdict == "PASS":
        passed, feedback = True, ""
    elif verdict == "FAIL":
        passed, feedback = False, rest.strip()
    else:
        # Unparseable verdict: an unknown review outcome must not be
        # recorded as PASS (fail-open). Skip this critique like the
        # soft-failure path above — clear any previous verdict, so neither
        # critique-passed? nor critique-failed? fires and revise is not
        # triggered — but leave a visible warning.
        svc.logger.warning(
            "critique_verdict_unparseable",
            session_id=session.id,
            stage="critique",
            raw=text[:200],
        )
        return StageOutcome(
            response=response,
            scratch_updates={"critique": None},
            token_usage=usage_total_tokens(verdict_response.usage),
        )
    return StageOutcome(
        response=response,
        scratch_updates={"critique": {"passed": passed, "feedback": feedback}},
        token_usage=usage_total_tokens(verdict_response.usage),
    )
