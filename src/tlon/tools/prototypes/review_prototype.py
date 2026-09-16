"""Heterogeneous peer review of staging proposals (issue , phase 2).

``reviewPrototype`` lets a member review a staged proposal: the caller
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
``success=False`` without writing a record, and the outer ReAct loop is
the retry mechanism (the ``proposeBehavior`` precedent).
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult
from tlon.tools.prototypes.ports import PrototypeError, PrototypeSummary

logger = get_logger(__name__)

VERDICTS = ("通过", "不通过", "弃权")
"""The three legal verdict markers — the first line of the review response
must be exactly one of them."""

_REVIEW_SYSTEM_PROMPT = (
    "你是行为原型的同行评审员。输入是一份 staging 区提案的全套材料："
    "behavior.tlon 全文、随包教训（LESSONS.md，如有）、谱系链"
    "（每一代的变更说明与相邻两代 behavior.tlon 的 diff）。\n"
    "以独立评审者的身份判断这份提案是否值得晋级为 trusted（可被 spawn）。\n"
    "评审角度：\n"
    "- spec 是否完整自足、形态合法（v1 schema 的 rules-form）；\n"
    "- 相对父代的变更是否有实质意义、与变更说明（proposal_note）一致；\n"
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


class ReviewPrototypeTool(Tool):
    """Tool to peer-review a staging proposal with one dedicated LLM call.

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
        lineage: Callable[[str], list[dict[str, Any]]],
        append_review: Callable[[str, str], None],
    ) -> None:
        super().__init__(
            name="reviewPrototype",
            description=(
                "评审一个 staging 区的原型提案（异质评审，）。只点名提案——"
                "结论不由你当场给：工具内部用你自己的模型做一次专项评审调用，"
                "读取提案的 behavior 全文、随包教训与谱系链（含每代 diff）后产出"
                " verdict（通过/不通过/弃权）与理由，追加记入提案目录的 REVIEW.md"
                "（含时间、你的 id 与你的模型）。评审是咨询性的：不自动晋级，"
                "人类审批时参考这些记录做决定。提案不存在或不在 staging 时失败。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "staging 区提案名称（trusted 库原型不可评审）",
                    },
                },
                "required": ["name"],
            },
        )
        self._reviewer_id = reviewer_id
        self._llm_client = llm_client
        self._detail = detail
        self._lineage = lineage
        self._append_review = append_review

    async def execute(self, **kwargs: Any) -> ToolResult:
        name: str = kwargs["name"]
        try:
            info, behavior, lessons, _reviews = self._detail(name)
            chain = self._lineage(name)
        except PrototypeError as e:
            return ToolResult(
                success=False, data=None, error=str(e), message=f"评审失败: {e}"
            )

        prompt_parts = [
            f"## 提案元数据\n名称：{info.name}；作者：{info.author or '未知'}；"
            f"第 {info.generation} 代"
            + (f"；变更说明：{info.proposal_note}" if info.proposal_note else ""),
            "## 行为规约（behavior.tlon 全文）\n" + behavior,
        ]
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
            return ToolResult(
                success=False,
                data={},
                error=f"评审响应首行不是合法 verdict（{'/'.join(VERDICTS)}）：{first_line!r}",
                message="评审调用的响应格式不合规，未记录，请重试",
            )

        model = self._llm_client.config.model_name
        timestamp = datetime.now(UTC).isoformat()
        entry = (
            f"## {timestamp} — {self._reviewer_id}（{model}）：{verdict}\n\n"
            f"{reason.strip()}\n"
        )
        try:
            self._append_review(name, entry)
        except PrototypeError as e:
            return ToolResult(
                success=False, data=None, error=str(e), message=f"评审记录落盘失败: {e}"
            )

        logger.info(
            "prototype_reviewed",
            name=name,
            reviewer=self._reviewer_id,
            model=model,
            verdict=verdict,
        )
        return ToolResult(
            success=True,
            data={
                "name": name,
                "verdict": verdict,
                "reviewer": self._reviewer_id,
                "model": model,
            },
            message=(
                f"评审已记入 '{name}' 的 REVIEW.md：{verdict}。"
                "评审是咨询性的——晋级与否由人类在 mirror 审批时决定。"
            ),
        )
