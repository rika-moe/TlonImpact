"""revise stage (phase 7c): feed critique feedback back into context."""

from typing import Any

from tlon.agents.session import AgentSession
from tlon.agents.stages.outcome import StageOutcome
from tlon.agents.stages.services import StageServices
from tlon.base.llm_client import LLMResponse


async def revise_stage(
    svc: StageServices,
    session: AgentSession,
    response: "LLMResponse | None",
    _opts: dict[str, Any] | None = None,
) -> StageOutcome:
    """revise stage (phase 7c): feed critique feedback back into context.

    No LLM call of its own — the feedback is declared as a user
    message so the next round's think revises naturally. No-op when
    there is no critique verdict or no feedback. Does not consume or
    produce the round's response.

    A consumed verdict is cleared (``scratch.critique`` set to nil):
    the verdict belongs to the round it reviewed, and clearing keeps a
    stale FAIL from re-triggering revise (duplicate feedback) or
    mis-gating downstream stages on later rounds where critique is
    conditionally skipped.
    """
    critique = session.loop_scratch.get("critique")
    feedback = critique.get("feedback") if isinstance(critique, dict) else None
    if not feedback:
        return StageOutcome(response=response)
    return StageOutcome(
        response=response,
        context_appends=[{"role": "user", "content": f"[评审反馈]\n\n{feedback}\n请据此修正。"}],
        scratch_updates={"critique": None},
    )
