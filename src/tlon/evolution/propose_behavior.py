"""Propose a next-generation behavior from lived experience (see
docs/design/prototypes.md).

``behavior-propose`` is the experience entry of the generation channel:
unlike ``prototype-write`` (a bare write channel where the caller supplies
the full spec text), this tool drafts the new spec itself — one dedicated
LLM call is fed the member's own ``config/behavior.tlon``, its memory
digest and distilled lessons, plus the caller's change intent. The draft
then lands in the staging area through the same ``write_staging_prototype``
gate, parented to the member's own prototype, so the trust model is
unchanged: nothing spawns until a human approves.

No retry inside the tool — validation/extraction failures come back
verbatim and the outer ReAct loop is the retry mechanism (the
``prototype-write`` precedent).
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.evolution.ports import PrototypeError, PrototypeSummary
from tlon.evolution.write_prototype import (
    _BEHAVIOR_HINT,
    NearKinCheck,
    _check_near_kin,
)
from tlon.operations.schema import RejectedError

if TYPE_CHECKING:
    from tlon.memory.manager import MemoryManager, SessionLogReader

logger = get_logger(__name__)

_DRAFT_SYSTEM_PROMPT = (
    "你是 behavior prototype 起草器。输入是一个成员的当前行为规约"
    "（behavior.tlon 底稿）、它的个体经验（记忆、蒸馏教训）"
    "和一条变更意图；输出是一份**完整的新 behavior.tlon**。\n"
    "要求：\n"
    "- 原型只描述怎样行动，不授予职业、职位或社会身份；行为显示名与描述应对应实际 spec。\n"
    "- 只输出 spec 文本本身——一个 v1 schema 的 EDN map；不要解释、"
    "不要注释、不要 Markdown 代码围栏。\n"
    "- 输出必须完整自足（不是 diff）：保留底稿中与意图无关的行为，"
    "只按意图做最小修改。\n"
    "- " + _BEHAVIOR_HINT
)


def _render_digest(digest: dict[str, Any]) -> str:
    """Render a memory digest as prompt text (claims)."""
    parts = [
        f"未蒸馏会话数：{digest['undistilled_session_count']}；记忆数：{digest['claim_count']}",
    ]
    if digest["claims"]:
        parts.append("### 记忆")
        for c in digest["claims"]:
            cid = str(c.get("id", ""))[:8]
            date = str(c.get("timestamp", ""))[:10]
            parts.append(f"- [{cid}] [{date}] {c.get('text', '')}")
    return "\n".join(parts)


def _extract_edn(content: str) -> str | None:
    """Extract the EDN spec from an LLM response.

    The model may wrap the spec in ``` fences despite the prompt; strip
    them, then take the first ``{`` through the last ``}``. Returns None
    when no EDN map is present (caller turns that into a retry-able
    failure for the outer loop).
    """
    text = content.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    return text[start : end + 1]


class BehaviorProposer:
    """Service to propose a next-generation behavior from lived experience.

    The member supplies only the change intent and a behavior label; the tool reads
    its own behavior spec and memory, drafts a complete new spec with one
    LLM call (purpose="propose"), and writes it to the staging area
    parented to the member's own prototype (``AgentConfig.prototype``;
    None = a new root). The proposal is untrusted: a human approves it in
    human review (ops/WebUI), then ``member-spawn`` derives the next generation.
    """

    def __init__(
        self,
        agent_id: str,
        prototype: str | None,
        author_id: str,
        llm_client: LLMClient,
        memory: "MemoryManager",
        logs: "SessionLogReader",
        writer: Callable[..., PrototypeSummary],
        tlon_paths: TlonPaths,
        solvency: Callable[[str], Awaitable[bool]] | None = None,
        near_kin: NearKinCheck | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._prototype = prototype
        self._author_id = author_id
        self._llm_client = llm_client
        self._memory = memory
        self._logs = logs
        self._writer = writer
        self._paths = tlon_paths
        # Solvency gate (): an insolvent bloodline loses proposal
        # eligibility. None = always eligible.
        self._solvency = solvency
        # Near-kin rejection (, agent side only): a reskin-level
        # duplicate of an existing prototype is turned away. None =
        # no check.
        self._near_kin = near_kin

    async def _solvent(self, prototype: str) -> bool:
        """Proposal-eligibility check for the author's bloodline (fail-open).

        A missing provider or any provider failure yields True — solvency
        bookkeeping must never block a proposal.
        """
        if self._solvency is None:
            return True
        try:
            return bool(await self._solvency(prototype))
        except Exception as e:
            logger.warning("solvency_check_failed", prototype=prototype, error=str(e))
            return True

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        if "name" in kwargs:
            raise RejectedError("name is system-generated; provide display_name instead", {})
        if not isinstance(kwargs.get("display_name"), str) or not kwargs["display_name"].strip():
            raise RejectedError("display_name must be a non-empty behavior label", {})
        if self._prototype is not None and not await self._solvent(self._prototype):
            raise RejectedError(f"bloodline '{self._prototype}' is insolvent", {})

        intent: str = kwargs["intent"]
        display_name: str = kwargs["display_name"]
        description: str = kwargs.get("description", "")

        behavior_file = self._paths.agent_behavior_file(self._agent_id)
        if not behavior_file.exists():
            raise RejectedError(f"member '{self._agent_id}' has no behavior.tlon", {})
        base_spec = behavior_file.read_text(encoding="utf-8")

        digest = await self._memory.get_memory_digest(self._logs)
        lessons_file = self._paths.memory_lessons_file(self._agent_id)
        lessons = lessons_file.read_text(encoding="utf-8") if lessons_file.exists() else None

        prompt_parts = [
            "## 当前行为规约（behavior.tlon）\n" + base_spec,
            "## 个体经验\n" + _render_digest(digest),
        ]
        if lessons:
            prompt_parts.append("## 蒸馏教训（lessons.md）\n" + lessons)
        prompt_parts.append("## 变更意图\n" + intent)

        response = await self._llm_client.chat(
            system_prompt=_DRAFT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "\n\n".join(prompt_parts)}],
            timeout=60.0,
            purpose="propose",
        )

        draft = _extract_edn(response.content or "")
        if draft is None:
            raise RejectedError("起草调用未产出 EDN spec（响应中没有 { ... } map）", {})

        hint = _BEHAVIOR_HINT
        # Near-kin rejection () sits after drafting: the draft text is
        # the thing being compared, so the check cannot run earlier. The
        # proposal's parent is always the member's own prototype, which is
        # exempt from the comparison.
        _check_near_kin(self._near_kin, draft, self._prototype)
        try:
            info = self._writer(
                draft,
                display_name=display_name,
                description=description,
                author_id=self._author_id,
                parent=self._prototype,
                proposal_note=intent,
                lessons=lessons,
            )
        except PrototypeError as e:
            raise RejectedError(str(e), {"hint": hint}) from e

        logger.info(
            "behavior_proposed",
            agent_id=self._agent_id,
            name=info.name,
            parent=info.parent,
            generation=info.generation,
        )
        return {
            "name": info.name,
            "parent": info.parent,
            "generation": info.generation,
            "trusted": info.trusted,
            "status": "pending_approval",
        }
