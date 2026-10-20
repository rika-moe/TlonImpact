"""Propose a capability word from lived experience (issue #30, see
docs/design/capabilities.md 「#30：突变晋升通道」).

``capability-propose`` is the experience entry of the capability channel —
the capability-side counterpart of ``behavior-propose``: a mutation is born
as a tlosure artifact, drafted by the agent directly from its intent and
its own experience (memory digest + distilled lessons; no behavior.tlon
draft — a capability word needs no behavior spec). One dedicated LLM call
(purpose="propose") produces the complete artifact, which then lands in
the staging area through the ``write_staging_capability`` gate (schema +
namespace/purity load gate + ``:examples`` sandbox assertions), so the
trust model is unchanged: nothing becomes trusted vocabulary until a
human approves.

No near-kin check (unlike ``behavior-propose``): near-kin guards against
reskin-level convergence of long behavior specs across bloodlines; the
capability vocabulary is a few dozen words, same-name is a hard
structural rejection, and reviewers see the full artifact plus its
``:examples`` assertions — a similarity subsystem would be a preventive
abstraction. The solvency gate is reused verbatim: proposal eligibility
follows bloodline solvency (the existing social contract).

No retry inside the tool — validation/extraction failures come back
verbatim (with the schema hint) and the outer ReAct loop is the retry
mechanism (the ``behavior-propose`` precedent).
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.evolution.ports import CapabilityProposalError, CapabilitySummary
from tlon.evolution.propose_behavior import _extract_edn, _render_digest
from tlon.operations.evolution import evolution_operation_definitions
from tlon.operations.filesystem import filesystem_operation_definitions
from tlon.operations.identity import identity_operation_definitions
from tlon.operations.lifecycle import lifecycle_operation_definitions
from tlon.operations.mail import mail_operation_definitions
from tlon.operations.memory import memory_operation_definitions
from tlon.operations.model import model_operation_definitions
from tlon.operations.observability import observability_operation_definitions
from tlon.operations.schema import RejectedError
from tlon.operations.shell import shell_operation_definitions
from tlon.operations.state import state_operation_definitions
from tlon.operations.tasks import task_operation_definitions
from tlon.operations.time import time_operation_definitions
from tlosure.builtins import ARITHMETIC_BUILTINS, CORE_BUILTINS
from tlosure.edn import Keyword, loads

if TYPE_CHECKING:
    from tlon.memory.manager import MemoryManager, SessionLogReader

logger = get_logger(__name__)

#: The pure vocabulary, enumerated from the same tlosure registries the
#: load gate composes (``CAPABILITY_PURE_BUILTINS`` in
#: ``agents/capabilities/spec.py`` — a test anchors the two equal).
_PURE_WORDS = sorted({**CORE_BUILTINS, **ARITHMETIC_BUILTINS})

#: The member action words a non-pure impl may additionally compose
#: Derived from host definitions, excluding controls and session orchestration.
#: A test anchors this projection against the capability admission registry.
_ACTION_WORDS: list[str] = []

for definitions in (
    evolution_operation_definitions,
    lifecycle_operation_definitions,
    model_operation_definitions,
    mail_operation_definitions,
    filesystem_operation_definitions,
    shell_operation_definitions,
    time_operation_definitions,
    memory_operation_definitions,
    task_operation_definitions,
    identity_operation_definitions,
    observability_operation_definitions,
    state_operation_definitions,
):
    _ACTION_WORDS.extend(operation.name for operation in definitions() if not operation.control)

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
    f"- :pure false 的 :impl 可另组合以下动作词：{' '.join(_ACTION_WORDS)}"
    "（返回操作真实结果；禁止封装 run-loop、finish-turn、sleep-until、exit-agent）。\n"
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


class CapabilityProposer:
    """Service to propose a capability word from lived experience.

    The member supplies only the intent and the word name; the tool reads
    its memory digest and distilled lessons, drafts a complete capability
    artifact with one LLM call (purpose="propose"), and writes it to the
    capability staging area. The proposal is untrusted: a human approves
    it (ops/WebUI), then the vocabulary bridge makes the word available
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
        self._agent_id = agent_id
        self._prototype = prototype
        self._author_id = author_id
        self._llm_client = llm_client
        self._memory = memory
        self._logs = logs
        self._writer = writer
        self._paths = tlon_paths
        # Solvency gate (same social contract as behavior-propose): an
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

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        if self._prototype is not None and not await self._solvent(self._prototype):
            raise RejectedError(f"bloodline '{self._prototype}' is insolvent", {})

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
            raise RejectedError("起草调用未产出 EDN artifact（响应中没有 { ... } map）", {})

        # The word name is the caller-chosen identity: the draft's :name
        # must match it exactly, otherwise the staging directory would
        # fork from the requested name — feed back for a re-draft.
        try:
            artifact = loads(draft)
        except ValueError as error:
            raise RejectedError(str(error), {"hint": _ARTIFACT_HINT}) from error
        if not isinstance(artifact, dict) or artifact.get(Keyword("name")) != name:
            raise RejectedError(f"起草产物的 :name 与指定词名 '{name}' 不一致", {})

        try:
            info = self._writer(
                draft,
                author_id=self._author_id,
                proposal_note=intent,
            )
        except CapabilityProposalError as e:
            raise RejectedError(str(e), {"hint": _ARTIFACT_HINT}) from e

        logger.info(
            "capability_proposed",
            agent_id=self._agent_id,
            name=info.name,
            author_id=self._author_id,
        )
        return {
            "name": info.name,
            "trusted": info.trusted,
            "status": "pending_approval",
        }
