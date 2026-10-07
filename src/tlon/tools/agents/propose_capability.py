"""Propose a capability word from lived experience (issue #30, see
docs/design/capabilities.md 「#30：突变晋升通道」).

``proposeCapability`` is the experience entry of the capability channel —
the capability-side counterpart of ``proposeBehavior``: a mutation is born
as a tlosure artifact, drafted by the agent directly from its intent and
its own experience (memory digest + distilled lessons; no behavior.tlon
draft — a capability word needs no behavior spec). One dedicated LLM call
(purpose="propose") produces the complete artifact, which then lands in
the staging area through the ``write_staging_capability`` gate (schema +
namespace/purity load gate + ``:examples`` sandbox assertions), so the
trust model is unchanged: nothing becomes trusted vocabulary until a
human approves.

No near-kin check (unlike ``proposeBehavior``): near-kin guards against
reskin-level convergence of long behavior specs across bloodlines; the
capability vocabulary is a few dozen words, same-name is a hard
structural rejection, and reviewers see the full artifact plus its
``:examples`` assertions — a similarity subsystem would be a preventive
abstraction. The solvency gate is reused verbatim: proposal eligibility
follows bloodline solvency (the existing social contract).

No retry inside the tool — validation/extraction failures come back
verbatim (with the schema hint) and the outer ReAct loop is the retry
mechanism (the ``proposeBehavior`` precedent).
"""

import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.tools.agents.ports import CapabilityProposalError, CapabilitySummary
from tlon.tools.agents.propose_behavior import _extract_edn, _render_digest
from tlon.tools.base import Tool, ToolResult
from tlosure.builtins import ARITHMETIC_BUILTINS, CORE_BUILTINS

if TYPE_CHECKING:
    from tlon.memory.manager import MemoryManager, SessionLogReader

logger = get_logger(__name__)

#: The pure vocabulary, enumerated from the same tlosure registries the
#: load gate composes (``CAPABILITY_PURE_BUILTINS`` in
#: ``agents/capabilities/spec.py`` — a test anchors the two equal).
_PURE_WORDS = sorted({**CORE_BUILTINS, **ARITHMETIC_BUILTINS})

#: The six member action words a non-pure impl may additionally compose
#: (``MEMBER_ACTION_NAMES`` in ``agents/vocabulary.py``; ``tlon.tools``
#: cannot import ``tlon.agents``, so the names are restated here under a
#: test anchor against the real registry).
_ACTION_WORDS = ["emit", "forward", "spawn", "start", "start-all", "stop"]

_ARTIFACT_HINT = (
    "artifact 是一个 v1 schema 的 EDN map，形状：\n"
    "{:version 1\n"
    ' :name "词名"\n'
    ' :description "一句话描述"\n'
    ' :args [{:name "mails" :type :list :doc "参数说明"}]\n'
    " :impl (fn [mails] body...)\n"
    " :pure true\n"
    ' :examples [{:args ["字面数据"] :expect 期望值}]}\n'
    "规则：\n"
    "- :name 是 artifact 定义的词：字母起头，后接字母/数字/-/_；"
    "一个 artifact 只定义一个词。\n"
    "- :args 逐项声明 {:name :type :doc}；:type ∈ "
    ":string/:number/:boolean/:list/:map；:impl 必须是 (fn [参数] body...) "
    "字面量，定长参数（不支持 & rest），参数数 == :args 数。\n"
    f"- :pure true 的 :impl 只能组合这 {len(_PURE_WORDS)} 个纯词："
    f"{' '.join(_PURE_WORDS)}。\n"
    f"- :pure false 的 :impl 可另组合六个动作词：{' '.join(_ACTION_WORDS)}"
    "（产出 ActionRequest 数据）。\n"
    "- :examples 仅 :pure true 可携带：{:args [字面数据] :expect 值} 的"
    "输入/输出断言，准入时在沙盒求值器跑——鼓励纯词携带 :examples 自证"
    "（评审看到的不只是声称做什么，还有做到了什么）。\n"
    "- 同名即拒：与受信库或 staging 区既有词同名直接拒绝；想改进既有词"
    "请换个新名字（词义不可变）。"
)

_DRAFT_SYSTEM_PROMPT = (
    "你是 capability（能力词）起草器。输入是一个成员的个体经验"
    "（记忆、蒸馏教训）、可选的既有受信词清单、指定的词名和一条能力意图；"
    "输出是一份**完整的 capability artifact**——突变出生即 tlosure artifact。\n"
    "要求：\n"
    "- 只输出 artifact 文本本身——一个 v1 schema 的 EDN map；不要解释、"
    "不要注释、不要 Markdown 代码围栏。\n"
    "- :name 必须恰好是指定的词名。\n"
    "- " + _ARTIFACT_HINT
)


class ProposeCapabilityTool(Tool):
    """Tool to propose a capability word from lived experience.

    The member supplies only the intent and the word name; the tool reads
    its memory digest and distilled lessons, drafts a complete capability
    artifact with one LLM call (purpose="propose"), and writes it to the
    capability staging area. The proposal is untrusted: a human approves
    it in the mirror, then the vocabulary bridge makes the word available
    to every member's spec on their next wake.
    """

    def __init__(
        self,
        agent_id: str,
        prototype: str | None,
        author_id: str,
        llm_client: LLMClient,
        memory: "MemoryManager",
        logs: "SessionLogReader",
        writer: Callable[..., CapabilitySummary],
        tlon_paths: TlonPaths,
        solvency: Callable[[str], Awaitable[bool]] | None = None,
        trusted_words: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(
            name="proposeCapability",
            description=(
                "基于自己的个体经验提出一个新能力词（capability，突变晋升通道的经验入口）。"
                "只需给出能力意图与词名：工具内部读你的记忆（claims 与蒸馏教训），"
                "由一次独立的起草调用产出完整的 capability artifact（tlosure "
                "(fn [...] ...) 实现），经 staging 闸写入（proposal_note 记意图）。"
                "同名即拒——想改进既有词请换个新名字。提案不可信：需人类在 mirror "
                "批准后，全社会成员下次 wake 才能在自己的 spec 里组合它。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "能力意图：想造一个什么词、解决什么问题（自然语言，记为 proposal_note）",
                    },
                    "name": {
                        "type": "string",
                        "description": "词名（字母起头，后接字母/数字/'-'/'_'；同名即拒，改进既有词请换新名）",
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
        # Solvency gate (same social contract as proposeBehavior): an
        # insolvent bloodline loses proposal eligibility. None = always
        # eligible.
        self._solvency = solvency
        # Trusted-library word listing rendered as prompt text
        # (anti-duplication + style sample). None / "" = empty library.
        self._trusted_words = trusted_words

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

        digest = await self._memory.get_memory_digest(self._logs)
        lessons_file = self._paths.memory_lessons_file(self._agent_id)
        lessons = lessons_file.read_text(encoding="utf-8") if lessons_file.exists() else None

        prompt_parts = ["## 个体经验\n" + _render_digest(digest)]
        if lessons:
            prompt_parts.append("## 蒸馏教训（lessons.md）\n" + lessons)
        trusted = self._trusted_words() if self._trusted_words is not None else ""
        if trusted:
            prompt_parts.append("## 既有受信词（勿重复造词；风格可参照）\n" + trusted)
        prompt_parts.append("## 词名\n" + name)
        prompt_parts.append("## 能力意图\n" + intent)

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
                error="起草调用未产出 EDN artifact（响应中没有 { ... } map）",
                message="起草失败：LLM 响应不含 artifact，请重试",
            )

        # The word name is the caller-chosen identity: the draft's :name
        # must match it exactly, otherwise the staging directory would
        # fork from the requested name — feed back for a re-draft.
        if not re.search(r':name\s+"' + re.escape(name) + r'"', draft):
            return ToolResult(
                success=False,
                data={},
                error=f"起草产物的 :name 与指定词名 '{name}' 不一致",
                message="起草失败：artifact 的 :name 不是指定的词名，请重起草",
            )

        try:
            info = self._writer(
                draft,
                author_id=self._author_id,
                proposal_note=intent,
            )
        except CapabilityProposalError as e:
            return ToolResult(
                success=False,
                data={"hint": _ARTIFACT_HINT},
                error=str(e),
                message=f"capability 校验失败，未写入: {e}",
            )

        logger.info(
            "capability_proposed",
            agent_id=self._agent_id,
            name=info.name,
            author_id=self._author_id,
        )
        return ToolResult(
            success=True,
            data={
                "name": info.name,
                "trusted": info.trusted,
                "status": "pending_approval",
            },
            message=(
                f"能力词 '{info.name}' 已写入 staging 区（不可信）。"
                "等待人类在 mirror 的能力审批中批准；批准后全社会成员下次 wake "
                "即可在自己的 spec 里组合它。"
            ),
        )
