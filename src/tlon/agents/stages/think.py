"""think stage: one LLM call producing the round's response."""

from typing import Any

from tlon.agents.session import AgentSession
from tlon.agents.stages.outcome import StageOutcome
from tlon.agents.stages.services import StageServices
from tlon.base.llm_client import LLMResponse


async def think_stage(
    svc: StageServices,
    session: AgentSession,
    _response: "LLMResponse | None" = None,
    _opts: dict[str, Any] | None = None,
) -> StageOutcome:
    """think stage: call the LLM once.

    On failure the error is recorded and the iteration advanced (both
    declared in the Outcome, applied by the engine), but no mail is
    sent here — the until expression terminates the loop through the
    unified path on a later step.
    """
    try:
        response = await svc.think(session.context, span="agent.think")
        session.set_last_llm_response(response)
    except Exception as e:
        svc.logger.error("session_llm_error", session_id=session.id, error=str(e), exc_info=True)
        return StageOutcome(record_error=True, advance_iteration=True)
    return StageOutcome(response=response)
