"""LLM-based distillation of run projections into claims (ADR 0017)."""

import json
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from tlon.base.config import LLMConfig
from tlon.base.execution import (
    BudgetExceededError,
    ExecutionDeadlineError,
    ModelRequestRejectedError,
    UnknownModelOutcomeError,
    current_execution,
)
from tlon.base.llm_client import LLMClient
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.operations.schema import RecoveryMismatchError

from .models import RunProjection

logger = get_logger(__name__)


class DistillationResponseError(ValueError):
    """A committed response is invalid and cannot be repaired by replaying it."""


class DistilledClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1)
    importance: float = Field(ge=0.0, le=1.0)

    @field_validator("text")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("claim text must not be blank")
        return value.strip()


class DistillationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    claims: list[DistilledClaim]


@dataclass
class ConflictVerdict:
    """The conflict judgment for one new claim (ADR 0017 stage 3).

    ``add``: no semantic conflict — the new claim is simply appended
    (this also covers coexistence with similar old claims).
    ``supersede``: the new claim updates/contradicts old ones; the old
    claims listed in ``superseded_ids`` are invalidated (never deleted).
    """

    action: Literal["add", "supersede"] = "add"
    superseded_ids: list[str] = field(default_factory=list)


class Distiller:
    """Uses LLM to distill completed-run projections into claims.

    One call per batch: the projections (rebuilt from the run
    journals) go in, a list of natural-language claims (text +
    importance) comes out. Empty claims are successful. Invalid responses
    preserve the batch and block replay until an operator resolves it.

    Example:
        >>> distiller = Distiller(llm_config)
        >>> claims = await distiller.distill(projections)
    """

    def __init__(
        self,
        llm_config: LLMConfig,
        agent_id: str | None = None,
        tlon_paths: TlonPaths | None = None,
    ):
        """Initialize distiller.

        Args:
            llm_config: Configuration for LLM client
            agent_id: Owning member id for ledger attribution (ADR 0014
                ); None = no accounting
            tlon_paths: Path resolver for the ledger file; required when
                ``agent_id`` is set (enforced by ``LLMClient``)
        """
        self.llm_client = LLMClient(llm_config, agent_id=agent_id, tlon_paths=tlon_paths)

    async def distill(self, runs: list[RunProjection]) -> list[dict[str, Any]]:
        """Distill run projections into claims.

        Args:
            runs: List of completed-run projections

        Returns:
            List of claim dictionaries with ``text`` and ``importance``;
            empty when the LLM explicitly returns no claims
        """
        if not runs:
            return []

        formatted_runs = self._format_runs(runs)

        system_prompt = """你是一个记忆蒸馏助手。你的任务是分析用户与AI助手之间的对话历史，蒸馏出值得长期记住的断言（claim）。

每条断言应该：
1. 是一句独立的自然语言陈述（中文），不依赖上下文也能读懂
2. 记录明确提及的重要信息：用户偏好、项目状态、关键决策、重要发现
3. 不要推测，不要记录琐碎的寒暄

每条断言包含：
- text: 断言文本
- importance: 重要程度 (0.0-1.0)，日常琐事 0.3 左右，关键决策/偏好 0.7 以上

请用 JSON 格式返回：
{
    "claims": [
        {"text": "用户偏好使用 Python 进行数据分析", "importance": 0.7}
    ]
}"""

        messages = [
            {"role": "user", "content": f"请从以下程序运行中蒸馏记忆断言：\n\n{formatted_runs}"}
        ]

        response = await self.llm_client.chat(
            system_prompt=system_prompt,
            messages=messages,
            timeout=30.0,
            purpose="distill",
            response_format={"type": "json_object"},
        )
        if response.finish_reason == "length":
            raise DistillationResponseError("distillation response truncated")
        if not response.content or response.tool_calls:
            raise DistillationResponseError("distillation requires a nonempty JSON response")
        try:
            result = DistillationResult.model_validate_json(response.content)
        except ValidationError as error:
            logger.error("distill_response_invalid", error=str(error))
            raise DistillationResponseError(f"invalid distillation JSON/schema: {error}") from error
        return [claim.model_dump() for claim in result.claims]

    def _format_runs(self, runs: list[RunProjection]) -> str:
        """Format run projections for LLM prompt."""
        lines = []

        for i, run in enumerate(runs, 1):
            lines.append(f"[程序运行 {i}] 来源：{run.run_id}；触发语：{run.trigger_content}")
            if run.program_result:
                lines.append(f"    程序返回：{run.program_result[:600]}")

            for resp in run.llm_responses:
                lines.append(f"    Agent: {resp[:200]}")

            for error in run.errors:
                lines.append(f"    [失败或未决] {error[:300]}")
            for tool_line in run.tool_results:
                lines.append(f"    [Tool] {tool_line[:150]}")

            lines.append("")

        return "\n".join(lines)

    def _extract_json(self, content: str) -> dict[str, Any]:
        """Extract JSON from LLM response."""
        content = content.strip()

        if "```json" in content:
            start = content.find("```json") + 7
            end = content.find("```", start)
            if end > start:
                content = content[start:end].strip()
        elif "```" in content:
            start = content.find("```") + 3
            end = content.find("```", start)
            if end > start:
                content = content[start:end].strip()

        return cast(dict[str, Any], json.loads(content))

    async def judge_conflicts(
        self,
        new_claims: list[str],
        candidates: list[dict[str, Any]],
    ) -> list[ConflictVerdict] | None:
        """Judge a batch of new claims against similar active old claims.

        One LLM call for the whole batch (cost control): the old
        candidates go in with id + date + text, the new claims numbered,
        and the model returns one verdict per new claim — ``ADD``
        (append, including coexistence) or ``SUPERSEDE`` with the ids of
        the old claims it invalidates (借鉴 Mem0 的 ADD/UPDATE 路由).

        Args:
            new_claims: Texts of the new claims, in order
            candidates: Similar active old claims (dicts carrying at
                least ``id``/``text``, ``timestamp`` used for the date)

        Returns:
            One :class:`ConflictVerdict` per new claim, in order — a
            malformed individual verdict degrades to ADD. None when the
            judgment itself failed (LLM error, unparseable response);
            the caller then appends everything without judgment
        """
        if not new_claims or not candidates:
            return [ConflictVerdict() for _ in new_claims]

        candidate_ids = {str(c.get("id")) for c in candidates}
        old_lines = []
        for c in candidates:
            date = str(c.get("timestamp", ""))[:10]
            old_lines.append(f"- id: {c.get('id')}（{date}）{c.get('text', '')}")
        new_lines = [f"{i}. {text}" for i, text in enumerate(new_claims, 1)]

        system_prompt = """你是记忆冲突判定助手。下面给出若干条「新记忆断言」和一批可能与之相关的「已有记忆断言」（每条含 id、记录日期、文本）。

请逐条判定每个新断言与已有断言的关系：
- ADD：新断言与所有已有断言都不矛盾——全新信息，或可与旧断言并存的补充——直接追加。
- SUPERSEDE：新断言与某些已有断言描述同一事实但内容更新或相反（例如偏好、状态、结论发生变化），被取代的旧断言应失效；superseded_ids 列出所有被取代的已有断言 id。

判定原则：
1. 只有语义上真正矛盾才算 SUPERSEDE；话题相似但内容不矛盾视为并存（ADD）。
2. superseded_ids 只能引用上面给出的已有断言 id，不要编造。
3. 严格输出 JSON，不要输出任何其他内容。

输出格式：
{"verdicts": [{"index": 1, "action": "ADD"}, {"index": 2, "action": "SUPERSEDE", "superseded_ids": ["..."]}]}"""

        messages = [
            {
                "role": "user",
                "content": (
                    "已有记忆断言：\n"
                    + "\n".join(old_lines)
                    + "\n\n新记忆断言：\n"
                    + "\n".join(new_lines)
                ),
            }
        ]

        try:
            response = await self.llm_client.chat(
                system_prompt=system_prompt,
                messages=messages,
                timeout=30.0,
                purpose="conflict_judge",
            )
            if not response.content:
                logger.warning("conflict_judge_response_empty")
                return None
            result = self._extract_json(response.content)
        except (
            BudgetExceededError,
            ExecutionDeadlineError,
            ModelRequestRejectedError,
            UnknownModelOutcomeError,
            RecoveryMismatchError,
        ):
            raise
        except Exception as e:
            if current_execution() is not None:
                raise
            logger.warning("conflict_judge_failed", error=str(e))
            return None

        verdicts_raw = result.get("verdicts")
        if not isinstance(verdicts_raw, list):
            logger.warning("conflict_judge_malformed_response")
            return None

        by_index: dict[int, ConflictVerdict] = {}
        for entry in verdicts_raw:
            verdict = self._parse_verdict(entry, candidate_ids)
            if verdict is not None:
                by_index[verdict[0]] = verdict[1]
        return [by_index.get(i, ConflictVerdict()) for i in range(1, len(new_claims) + 1)]

    @staticmethod
    def _parse_verdict(entry: Any, candidate_ids: set[str]) -> tuple[int, ConflictVerdict] | None:
        """Parse one raw verdict; malformed entries parse as None and the
        corresponding new claim degrades to ADD."""
        if not isinstance(entry, dict):
            return None
        raw_index = entry.get("index")
        if not isinstance(raw_index, (int, str)):
            return None
        try:
            index = int(raw_index)
        except ValueError:
            return None
        action = str(entry.get("action", "")).upper()
        if action != "SUPERSEDE":
            return index, ConflictVerdict()
        raw_ids = entry.get("superseded_ids")
        superseded_ids = [
            str(old_id)
            for old_id in (raw_ids if isinstance(raw_ids, list) else [])
            if str(old_id) in candidate_ids
        ]
        if not superseded_ids:
            # SUPERSEDE without any valid target is meaningless — treat
            # as ADD (the safe direction: never invalidate on a guess).
            return index, ConflictVerdict()
        return index, ConflictVerdict(action="supersede", superseded_ids=superseded_ids)
