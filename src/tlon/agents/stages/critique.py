"""critique stage (phase 7c): review the round's latest output."""

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

    Parses the verdict's first line: PASS -> passed; FAIL -> failed
    with the remaining text as feedback; anything unparseable passes
    (lenient default — critique never blocks the main flow). The
    verdict is declared for the loop scratchpad. Fails soft like plan;
    does not consume or produce the round's response.
    """
    try:
        verdict_response = await svc.think(
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
            ],
            span="agent.critique",
        )
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
    if verdict.startswith("FAIL"):
        passed, feedback = False, rest.strip()
    else:
        # PASS or unparseable: lenient default, never block the flow.
        passed, feedback = True, ""
    return StageOutcome(
        response=response,
        scratch_updates={"critique": {"passed": passed, "feedback": feedback}},
    )
