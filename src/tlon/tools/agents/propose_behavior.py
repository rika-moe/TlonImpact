"""Propose a next-generation behavior from lived experience (see
docs/design/prototypes.md).

``proposeBehavior`` is the experience entry of the generation channel:
unlike ``writePrototype`` (a bare write channel where the caller supplies
the full spec text), this tool drafts the new spec itself — one dedicated
LLM call is fed the member's own ``config/behavior.tlon``, its memory
digest and distilled lessons, plus the caller's change intent. The draft
then lands in the staging area through the same ``write_staging_prototype``
gate, parented to the member's own prototype, so the trust model is
unchanged: nothing spawns until a human approves.

No retry inside the tool — validation/extraction failures come back
verbatim and the outer ReAct loop is the retry mechanism (the
``writePrototype`` precedent).
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.tools.agents.write_prototype import (
    _BEHAVIOR_HINT,
    _SCRIPT_HINT,
    NearKinCheck,
    _check_near_kin,
)
from tlon.tools.base import Tool, ToolResult
from tlon.tools.prototypes.ports import PrototypeError, PrototypeSummary

if TYPE_CHECKING:
    from tlon.memory.manager import MemoryManager, SessionLogReader

logger = get_logger(__name__)

_DRAFT_SYSTEM_PROMPT = (
    "你是 behavior prototype 起草器。输入是一个成员的当前行为规约"
    "（behavior.tlon 底稿）、它的个体经验（记忆、蒸馏教训）"
    "和一条变更意图；输出是一份**完整的新 behavior.tlon**。\n"
    "要求：\n"
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


class ProposeBehaviorTool(Tool):
    """Tool to propose a next-generation behavior from lived experience.

    The member supplies only the change intent and a name; the tool reads
    its own behavior spec and memory, drafts a complete new spec with one
    LLM call (purpose="propose"), and writes it to the staging area
    parented to the member's own prototype (``AgentConfig.prototype``;
    None = a new root). The proposal is untrusted: a human approves it in
    human review (ops/WebUI), then ``spawnAgent`` derives the next generation.
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
        super().__init__(
            name="proposeBehavior",
            description=(
                "基于自己的个体经验提出下一代行为原型（世代通道的经验入口）。"
                "只需给出变更意图与新原型名：工具内部读你当前的 behavior.tlon 与记忆"
                "（claims 与蒸馏教训），由一次独立的起草调用产出完整的新 spec，"
                "并以你自己的 prototype 为父原型写入 staging 区（proposal_note 记意图、"
                "lessons 随包）。与 writePrototype 的区别：那是裸写通道（自带全文），"
                "这里由经验驱动起草。提案不可信：需人类批准（ops/WebUI）后才能 spawnAgent "
                "派生新世代。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "变更意图：想改什么、为什么（自然语言，记为 proposal_note）",
                    },
                    "name": {
                        "type": "string",
                        "description": "新原型名称（字母/数字/'-'/'_'，全局唯一）",
                    },
                    "display_name": {
                        "type": "string",
                        "description": "显示名（人类审批时看到）",
                    },
                    "description": {
                        "type": "string",
                        "description": "一句话描述这个原型的行为定位",
                    },
                },
                "required": ["intent", "name"],
            },
        )
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

    async def execute(self, **kwargs: Any) -> ToolResult:
        if self._prototype is not None and not await self._solvent(self._prototype):
            return ToolResult(
                success=False,
                data={},
                error=f"bloodline '{self._prototype}' is insolvent",
                message=(
                    f"血脉 '{self._prototype}' 已失去偿付能力（长期入不敷出），提案资格被暂停。"
                    "先用手上成员做出被 endorse 的产出，恢复偿付能力后再提案。"
                ),
            )

        intent: str = kwargs["intent"]
        name: str = kwargs["name"]
        display_name: str = kwargs.get("display_name", "")
        description: str = kwargs.get("description", "")

        behavior_file = self._paths.agent_behavior_file(self._agent_id)
        if not behavior_file.exists():
            return ToolResult(
                success=False,
                data={},
                error=f"member '{self._agent_id}' has no behavior.tlon",
                message="找不到自己的行为规约底稿，无法起草",
            )
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
            return ToolResult(
                success=False,
                data={},
                error="起草调用未产出 EDN spec（响应中没有 { ... } map）",
                message="起草失败：LLM 响应不含 spec，请重试",
            )

        hint = _BEHAVIOR_HINT if ":loop" in draft else _SCRIPT_HINT
        # Near-kin rejection () sits after drafting: the draft text is
        # the thing being compared, so the check cannot run earlier. The
        # proposal's parent is always the member's own prototype, which is
        # exempt from the comparison.
        near_kin_rejection = _check_near_kin(self._near_kin, draft, self._prototype)
        if near_kin_rejection is not None:
            return near_kin_rejection
        try:
            info = self._writer(
                name,
                draft,
                display_name=display_name,
                description=description,
                author_id=self._author_id,
                parent=self._prototype,
                proposal_note=intent,
                lessons=lessons,
            )
        except PrototypeError as e:
            return ToolResult(
                success=False,
                data={"hint": hint},
                error=str(e),
                message=f"prototype 校验失败，未写入: {e}",
            )

        logger.info(
            "behavior_proposed",
            agent_id=self._agent_id,
            name=info.name,
            parent=info.parent,
            generation=info.generation,
        )
        return ToolResult(
            success=True,
            data={
                "name": info.name,
                "parent": info.parent,
                "generation": info.generation,
                "trusted": info.trusted,
                "status": "pending_approval",
            },
            message=(
                f"提案 '{info.name}' 已写入 staging 区（不可信，第 {info.generation} 代）。"
                "等待人类在「规约审批」（ops/WebUI）中批准；批准后用 spawnAgent 派生新世代。"
            ),
        )
