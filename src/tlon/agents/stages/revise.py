"""revise stage (phase 7c): feed critique feedback back into context."""

from typing import Any

from tlon.agents.session import AgentSession
from tlon.agents.stages.draft import render_review_output
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
    there is no failed critique verdict. Does not consume or
    produce the round's response.

    A consumed verdict is cleared (``scratch.critique`` set to nil):
    the verdict belongs to the round it reviewed, and clearing keeps a
    stale FAIL from re-triggering revise (duplicate feedback) or
    mis-gating downstream stages on later rounds where critique is
    conditionally skipped.
    """
    critique = session.loop_scratch.get("critique")
    if not isinstance(critique, dict) or critique.get("passed") is not False:
        return StageOutcome(response=response)
    feedback = critique.get("feedback") or "评审未给出具体意见。"
    parts = [f"[评审反馈]\n\n未通过评审。\n{feedback}"]
    if response is not None:
        draft = render_review_output(session, response, blocked=True)
        if draft:
            parts.append(f"[本轮被评审的完整输出与运行时状态]\n{draft}")
    parts.append(
        "评审意见不构成执行回执；请以逐项运行时状态为准修正。"
        "勿把未执行草稿描述为前函或已发送邮件，勿把已派发或已有回执的调用描述为未执行。"
    )
    return StageOutcome(
        response=response,
        context_appends=[{"role": "user", "content": "\n\n".join(parts)}],
        scratch_updates={"critique": None},
    )
