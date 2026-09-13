"""think stage: one LLM call producing the round's response."""

from typing import Any

from tlon.agents.session import AgentSession
from tlon.agents.stages.outcome import StageOutcome
from tlon.agents.stages.services import StageServices
from tlon.base.llm_client import ContextWindowExceededError, LLMResponse


async def think_stage(
    svc: StageServices,
    session: AgentSession,
    _response: "LLMResponse | None" = None,
    _opts: dict[str, Any] | None = None,
) -> StageOutcome:
    """think stage: call the LLM once.

    The response is declared in the Outcome; the engine records it as
    the session's last LLM response when applying it. A successful call
    also declares ``clear_errors`` — think is the loop's only error
    source, so a success here ends the error streak and the engine
    zeroes the consecutive error count (``max_consecutive_errors``
    bounds streaks, not lifetime totals). On failure the error is
    recorded and the iteration advanced (both declared in the Outcome,
    applied by the engine), but no mail is sent here — the until
    expression terminates the loop through the unified path on a
    later step.

    A ContextWindowExceededError (ADR 0008) is NOT swallowed into the
    record_error fallback: it re-raises so the engine can force-compact
    and retry the stage once before the fatal path takes over.
    """
    try:
        response = await svc.think(session.context, span="agent.think", session_id=session.id)
    except ContextWindowExceededError:
        raise
    except Exception as e:
        svc.logger.error("session_llm_error", session_id=session.id, error=str(e), exc_info=True)
        return StageOutcome(record_error=True, advance_iteration=True)
    return StageOutcome(response=response, clear_errors=True)
