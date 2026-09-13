"""plan stage (phase 7c): one planning LLM call before the round."""

from typing import Any

from tlon.agents.session import AgentSession
from tlon.agents.stages.outcome import StageOutcome
from tlon.agents.stages.services import StageServices
from tlon.base.llm_client import ContextWindowExceededError, LLMResponse


async def plan_stage(
    svc: StageServices,
    session: AgentSession,
    response: "LLMResponse | None",
    opts: dict[str, Any] | None = None,
) -> StageOutcome:
    """plan stage (phase 7c): one planning LLM call before the round.

    The plan text is declared for the loop scratchpad and for the
    conversation as a user message (the engine applies both). Fails
    soft: an LLM error skips the stage with a warning — no scratch
    write, no error recorded (auxiliary stages degrade, they never
    blow up the session). Does not consume or produce the round's
    response.
    """
    max_steps = (opts or {}).get("max-steps", 5)
    try:
        plan_response = await svc.think(
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
            ],
            span="agent.plan",
            purpose="plan",
            session_id=session.id,
        )
    except ContextWindowExceededError:
        # ADR 0008: not a soft failure — the engine force-compacts and
        # retries this stage once.
        raise
    except Exception as e:
        svc.logger.warning("loop_stage_failed", session_id=session.id, stage="plan", error=str(e))
        return StageOutcome(response=response)
    plan_text = plan_response.content or ""
    return StageOutcome(
        response=response,
        context_appends=[{"role": "user", "content": f"[执行计划]\n\n{plan_text}"}],
        scratch_updates={"plan": plan_text},
    )
