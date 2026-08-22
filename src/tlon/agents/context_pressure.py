"""Context pressure management — storage untouched, view-side graded relief
(ADR 0008).

``session.context`` grows monotonically within a session; without relief a
long session ends in a context-length 4xx. This module is the engine-layer
seam (invoked by LoopDriver before each step and on a
``ContextWindowExceededError``) that shrinks the *model-facing view* in two
grades:

1. **prune** (zero LLM cost): every ``role=tool`` message between the anchor
   and the keep window gets its content replaced in place with a short
   placeholder (``tool_call_id`` kept, the tool_call/tool pair never split).
   Re-estimate; done when under the threshold.
2. **summary** (LLM fallback): the middle segment (after the anchor, before
   the keep window, pruned messages included) is summarized into one
   ``[前文摘要]`` user message replacing the segment in place. Running
   summary: a previous ``[前文摘要]`` at the head of the middle is part of
   the summary input, so summaries roll forward instead of stacking. A
   failing summarizer degrades to a plain placeholder message.

The anchor (``session.anchor_len`` = the initial context block journaled with
``session/start``) is never touched. Every compaction is journaled as
``context/compact`` with the full post-compaction context *before* the
in-memory session is mutated — the journal stays the sole truth (ADR 0007).
"""

import json
from typing import Any

from tlon.agents.session import AgentSession
from tlon.agents.session_journal import SessionJournal
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger

logger = get_logger(__name__)

#: Keep window: the most recent messages never touched by compaction.
KEEP_RECENT = 20
#: Keep window under force (the emergency path after a context-length 4xx).
KEEP_RECENT_FORCE = 10

#: Placeholder replacing a pruned tool result's content in place.
PRUNED_PLACEHOLDER = json.dumps(
    {"_pruned": True, "note": "历史工具结果已清理"}, ensure_ascii=False
)

#: Content prefix marking a summary message (also the running-summary marker).
SUMMARY_MARKER = "[前文摘要]"

#: Summarizer prompts (Chinese; keep goals, actions/results, decisions,
#: open questions — recall first, precision later).
_SUMMARY_SYSTEM_PROMPT = (
    "你是对话历史压缩器。把给定的对话中段压缩成一份中文摘要，供后续模型继续任务时阅读。"
    "必须保留：任务目标、已完成的动作与结果、关键决策、未决问题。"
    "丢弃寒暄、重复与已失效的中间过程。只输出摘要本身。"
)
_SUMMARY_INSTRUCTION = "请将以上对话历史压缩为一段摘要。"

#: Fallback message when the summarizer call itself fails.
_SUMMARY_FALLBACK = f"{SUMMARY_MARKER}\n\n历史消息摘要失败，已省略。"

#: CJK ranges estimated at 1 token per character.
_CJK_RANGES = (("一", "鿿"), ("　", "〿"), ("＀", "￯"))


def _is_cjk(ch: str) -> bool:
    return any(lo <= ch <= hi for lo, hi in _CJK_RANGES)


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Heuristic token estimate for a context message list.

    The list is JSON-serialized whole; CJK characters count 1 token each,
    everything else 4 characters per token (rounded up — overestimating is
    safe: triggering early beats triggering late).
    """
    text = json.dumps(messages, ensure_ascii=False, default=str)
    cjk = sum(1 for ch in text if any(lo <= ch <= hi for lo, hi in _CJK_RANGES))
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def compaction_threshold(context_window: int) -> int:
    """Trigger threshold (tokens): fixed 40k reserve, percentage backstop."""
    return max(context_window - 40_000, int(context_window * 0.8))


class ContextPressure:
    """Graded context relief for one agent's sessions (engine layer).

    Invoked by LoopDriver: proactively before each step, and with
    ``force=True`` after a ``ContextWindowExceededError``. Compaction is
    journaled before the in-memory session changes (model-visible ⇒
    journaled).
    """

    def __init__(self, llm_client: LLMClient, journal: SessionJournal, context_window: int):
        """Args:
        llm_client: LLM client for the grade-2 summary call
        journal: Session journal — compact events land here first
        context_window: The model's context window (LLMConfig.context_window)
        """
        self._llm_client = llm_client
        self._journal = journal
        self._threshold = compaction_threshold(context_window)

    async def maybe_compact(self, session: AgentSession, *, force: bool = False) -> bool:
        """Compact the session's context when over the threshold.

        Returns True when the context was compacted. With ``force=True``
        the threshold check is skipped and the keep window tightens to
        :data:`KEEP_RECENT_FORCE` (the emergency path after a
        context-length 4xx). Never touches the anchor; never splits a
        tool_call/tool pair. Raises only when the journal append fails —
        the in-memory session is then left untouched (ADR 0007 invariant).
        """
        context = session.context
        tokens_before = estimate_tokens(context)
        if not force and tokens_before <= self._threshold:
            return False

        anchor = min(session.anchor_len, len(context))
        keep = KEEP_RECENT_FORCE if force else KEEP_RECENT
        window_start = max(anchor, len(context) - keep)
        # A tool message at the window head belongs to the assistant message
        # before it — extend the window back so the pair is never split.
        while window_start > anchor and context[window_start].get("role") == "tool":
            window_start -= 1
        middle = context[anchor:window_start]
        if not middle:
            # Anchor + keep window alone exceed the threshold; nothing to
            # relieve without touching protected messages.
            logger.warning(
                "context_pressure_no_middle",
                session_id=session.id,
                tokens=tokens_before,
                threshold=self._threshold,
            )
            return False

        reason = "context_window_exceeded" if force else "threshold_exceeded"

        # Grade 1: prune tool results in the middle (zero LLM cost).
        pruned = [dict(message) for message in context]
        for i in range(anchor, window_start):
            if pruned[i].get("role") == "tool":
                pruned[i]["content"] = PRUNED_PLACEHOLDER
        tokens_after = estimate_tokens(pruned)
        if tokens_after <= self._threshold:
            await self._commit(session, "prune", reason, tokens_before, tokens_after, pruned)
            return True

        # Grade 2: summarize the (pruned) middle into one [前文摘要] message.
        summarized = await self._summarize(session, pruned, anchor, window_start)
        tokens_after = estimate_tokens(summarized)
        await self._commit(session, "summary", reason, tokens_before, tokens_after, summarized)
        return True

    async def _summarize(
        self, session: AgentSession, context: list[dict[str, Any]], anchor: int, window_start: int
    ) -> list[dict[str, Any]]:
        """Replace the middle segment with one summary message.

        The middle is the summary input — a previous ``[前文摘要]`` at its
        head folds in naturally (running summary, no stacking). A failed
        summarizer degrades to a placeholder message instead of raising:
        losing history detail beats losing the session.
        """
        middle = context[anchor:window_start]
        try:
            response = await self._llm_client.chat(
                system_prompt=_SUMMARY_SYSTEM_PROMPT,
                messages=[*middle, {"role": "user", "content": _SUMMARY_INSTRUCTION}],
            )
            summary_text = (response.content or "").strip()
            if not summary_text:
                raise ValueError("empty summary")
            message = {"role": "user", "content": f"{SUMMARY_MARKER}\n\n{summary_text}"}
        except Exception as e:
            logger.warning(
                "context_summary_failed", session_id=session.id, error=str(e), exc_info=True
            )
            message = {"role": "user", "content": _SUMMARY_FALLBACK}
        return [*context[:anchor], message, *context[window_start:]]

    async def _commit(
        self,
        session: AgentSession,
        kind: str,
        reason: str,
        tokens_before: int,
        tokens_after: int,
        new_context: list[dict[str, Any]],
    ) -> None:
        """Journal the compaction, then mutate the session — never the reverse."""
        await self._journal.append(
            session.id,
            "context/compact",
            kind=kind,
            reason=reason,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            context=new_context,
        )
        session.context = new_context
        logger.info(
            "context_compacted",
            session_id=session.id,
            kind=kind,
            reason=reason,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
        )
