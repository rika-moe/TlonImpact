"""Heterogeneous peer review of staging proposals (see
docs/design/prototypes.md).

``prototype-review`` lets a member review a staged proposal: the caller
only names the proposal — the verdict is NOT caller-supplied. The tool
makes one dedicated review call with the reviewer's own ``llm_client``
(purpose="review"), so the record format is uniform and the heterogeneity
comes from the reviewer's own model bloodline (ADR 0014: members of
different model lineages review each other), not from ad-hoc verdicts at
call time.

The record lands as an appended entry in the staging directory's
``REVIEW.md``; approval's whole-directory move carries it into the trusted
library for free. Reviews are advisory — they never gate ``approve``;
the human reads them on the detail endpoint when deciding.

No retry inside the tool — an unparseable verdict comes back as
a known rejection without writing a record, and the outer ReAct loop is
the retry mechanism (the ``behavior-propose`` precedent).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.evolution.ports import PrototypeError, PrototypeSummary
from tlon.operations.schema import RejectedError

logger = get_logger(__name__)

VERDICTS = ("通过", "不通过", "弃权")
"""The three legal verdict markers — the first line of the review response
must be exactly one of them."""

_REVIEW_SYSTEM_PROMPT = (
    "你是行为原型的同行评审员。输入是一份 staging 区提案的全套材料："
    "behavior.tlon 与全部模块全文、随包教训（LESSONS.md，如有）、谱系链"
    "（每一代的变更说明与相邻两代 behavior.tlon 的 diff）。\n"
    "以独立评审者的身份判断这份提案是否值得晋级为 trusted（可被 spawn）。\n"
    "评审角度：\n"
    "- 显示名与描述是否对应 spec 中的实际行为，不以职业或职位代替行为差异；原型不授予社会身份；\n"
    "- spec 是否完整自足、形态合法（v2 handlers 及完整模块依赖（历史 v1 按其原规约审阅））；\n"
    "- 相对父代的变更是否有实质意义、与变更说明（proposal_note）一致；\n"
    "- 执行能力声明（:execution :level）是否名副其实——L0/L1 足以满足需求"
    "却声明 L2（Docker 完整 Linux userland）是评审红旗；\n"
    "- 是否有明显危险行为（越权访问、资源滥用、欺骗）或与既有原型换皮级雷同。\n"
    "输出格式（严格遵守）：第一行恰好是三个标记之一——通过 / 不通过 / 弃权；"
    "第二行起为评审理由（简洁）。材料不足以判断时输出 弃权。"
)


def _render_lineage(chain: list[dict[str, Any]]) -> str:
    """Render the derived lineage chain (root first) as prompt text."""
    parts = []
    for entry in chain:
        author = entry["author"] or "人类"
        parts.append(
            f"### 第 {entry['generation']} 代：{entry['name']}"
            f"（作者 {author}，trusted={entry['trusted']}）"
        )
        if entry["proposal_note"]:
            parts.append(f"变更说明：{entry['proposal_note']}")
        if entry["broken"]:
            parts.append("（谱系链在此截断：父原型已删除/改名或链异常）")
        if entry["diff_from_parent"]:
            parts.append("```diff\n" + entry["diff_from_parent"] + "```")
    return "\n".join(parts)


class PrototypeReviewer:
    """Service to peer-review a staging proposal with one dedicated LLM call.

    The injected ``detail`` / ``lineage`` / ``append_review`` callables are
    the domain functions of ``agents.prototypes`` (dependency inversion,
     — tools never import ``tlon.agents``), with ``staging=True`` and
    the paths baked in by the assembler. The reviewer identity and model
    recorded in ``REVIEW.md`` come from the tool's own configuration
    (``reviewer_id`` + ``llm_client.config.model_name``), so the review
    trail shows **who** judged and **with which model bloodline**.
    """

    def __init__(
        self,
        reviewer_id: str,
        llm_client: LLMClient,
        detail: Callable[..., tuple[PrototypeSummary, str, str | None, str | None]],
        modules: Callable[[str], dict[str, str]],
        lineage: Callable[[str], list[dict[str, Any]]],
        append_review: Callable[[str, str], None],
    ) -> None:
        self._reviewer_id = reviewer_id
        self._llm_client = llm_client
        self._detail = detail
        self._modules = modules
        self._lineage = lineage
        self._append_review = append_review

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        name: str = kwargs["name"]
        try:
            info, behavior, lessons, _reviews = self._detail(name)
            modules = self._modules(name)
            chain = self._lineage(name)
        except PrototypeError as e:
            raise RejectedError(str(e), {}) from e

        prompt_parts = [
            f"## 提案元数据\nID：{info.name}；行为显示名：{info.display_name}；描述：{info.description}；作者：{info.author or '未知'}；"
            f"第 {info.generation} 代"
            + (f"；变更说明：{info.proposal_note}" if info.proposal_note else ""),
            "## 行为规约（behavior.tlon 全文）\n" + behavior,
        ]
        for path, source in sorted(modules.items()):
            prompt_parts.append(f"## 模块 modules/{path}\n{source}")
        if lessons:
            prompt_parts.append("## 随包教训（LESSONS.md）\n" + lessons)
        prompt_parts.append("## 谱系链（根在前，含每代 diff）\n" + _render_lineage(chain))

        response = await self._llm_client.chat(
            system_prompt=_REVIEW_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": "\n\n".join(prompt_parts)}],
            timeout=60.0,
            purpose="review",
        )

        content = (response.content or "").strip()
        first_line, _, reason = content.partition("\n")
        verdict = first_line.strip()
        if verdict not in VERDICTS:
            raise RejectedError(
                f"评审响应首行不是合法 verdict（{'/'.join(VERDICTS)}）：{first_line!r}", {}
            )

        model = self._llm_client.config.model_name
        timestamp = datetime.now(UTC).isoformat()
        entry = f"## {timestamp} — {self._reviewer_id}（{model}）：{verdict}\n\n{reason.strip()}\n"
        try:
            self._append_review(name, entry)
        except PrototypeError as e:
            raise RejectedError(str(e), {}) from e

        logger.info(
            "prototype_reviewed",
            name=name,
            reviewer=self._reviewer_id,
            model=model,
            verdict=verdict,
        )
        return {
            "name": name,
            "verdict": verdict,
            "reviewer": self._reviewer_id,
            "model": model,
        }
