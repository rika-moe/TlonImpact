"""reviewPrototype — heterogeneous peer review of staging proposals ().

The caller only names the proposal; the verdict comes from one dedicated
review call with the reviewer's own ``llm_client`` (purpose="review"),
so the record format is uniform and the heterogeneity is the reviewer's
model bloodline. Records append to the staging directory's ``REVIEW.md``
and ride along with approval's whole-directory move into the trusted
library. An unparseable verdict is ``success=False`` with nothing
written — the outer ReAct loop is the retry mechanism.
"""

from pathlib import Path
from typing import Any

import pytest

from tlon.agents.prototypes.catalog import get_prototype_detail
from tlon.agents.prototypes.lineage import get_prototype_lineage
from tlon.agents.prototypes.management import write_trusted_prototype
from tlon.agents.prototypes.staging import (
    append_staging_review,
    approve_prototype,
    write_staging_prototype,
)
from tlon.base.config import LLMConfig
from tlon.base.llm_client import LLMResponse
from tlon.base.paths import TlonPaths
from tlon.tools.prototypes.review_prototype import ReviewPrototypeTool

_BASE_SPEC = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions [(forward {:to ["x@tlon.internal"]})]}]}
"""

_CHILD_SPEC = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions [(forward {:to ["y@tlon.internal"]})]}]}
"""


class _FakeLLM:
    """Duck-typed LLMClient returning one canned review response."""

    def __init__(self, content: str, model: str = "fake/reviewer-model") -> None:
        self._content = content
        self.config = LLMConfig(model_name=model)
        self.calls: list[dict[str, Any]] = []

    async def chat(self, **kwargs: Any) -> LLMResponse:
        self.calls.append(kwargs)
        return LLMResponse(content=self._content)


def _make_tool(p: TlonPaths, reviewer_id: str, llm: _FakeLLM) -> ReviewPrototypeTool:
    return ReviewPrototypeTool(
        reviewer_id=reviewer_id,
        llm_client=llm,  # type: ignore[arg-type]
        detail=lambda name: get_prototype_detail(name, staging=True, tlon_paths=p),
        lineage=lambda name: get_prototype_lineage(name, tlon_paths=p),
        append_review=lambda name, entry: append_staging_review(name, entry, tlon_paths=p),
    )


def _stage_child(p: TlonPaths, name: str = "base-gen2") -> None:
    write_trusted_prototype("base", _BASE_SPEC, tlon_paths=p)
    write_staging_prototype(
        name,
        _CHILD_SPEC,
        author_id="c-base-00000001",
        parent="base",
        proposal_note="把转发目标换成 y",
        lessons="- 别在深夜回信\n",
        tlon_paths=p,
    )


class TestReviewPrototype:
    async def test_main_path(self, tmp_path: Path) -> None:
        """评审调用 → REVIEW.md 落盘：字段齐（评审者/模型/verdict/理由），
        回执正确；prompt 携带 behavior 全文、lessons、谱系 diff、变更说明。"""
        p = TlonPaths(home=tmp_path)
        _stage_child(p)
        llm = _FakeLLM("通过\n变更与 proposal_note 一致，spec 完整自足。")
        tool = _make_tool(p, "c-scribe-00000002", llm)

        result = await tool.execute(name="base-gen2")

        assert result.success, result.error
        assert result.data == {
            "name": "base-gen2",
            "verdict": "通过",
            "reviewer": "c-scribe-00000002",
            "model": "fake/reviewer-model",
        }

        review_file = p.prototypes_staging_dir() / "base-gen2" / "REVIEW.md"
        text = review_file.read_text(encoding="utf-8")
        assert text.startswith("# 评审记录\n")
        assert "c-scribe-00000002" in text
        assert "fake/reviewer-model" in text
        assert "：通过" in text
        assert "变更与 proposal_note 一致" in text

        # 评审调用：purpose 记账 + 材料齐（behavior 全文、lessons、谱系 diff、变更说明）
        assert len(llm.calls) == 1
        call = llm.calls[0]
        assert call["purpose"] == "review"
        assert "temperature" not in call
        assert call["timeout"] == 60.0
        prompt = call["messages"][0]["content"]
        assert _CHILD_SPEC in prompt
        assert "- 别在深夜回信" in prompt
        assert "把转发目标换成 y" in prompt
        assert "x@tlon.internal" in prompt and "y@tlon.internal" in prompt  # diff 双端
        assert "第 1 代：base" in prompt and "第 2 代：base-gen2" in prompt

    async def test_reviews_accumulate(self, tmp_path: Path) -> None:
        """三次评审追加不覆盖：REVIEW.md 依序含三条记录，表头只出现一次。"""
        p = TlonPaths(home=tmp_path)
        _stage_child(p)
        llm = _FakeLLM("不通过\n理由甲。")
        tool = _make_tool(p, "r-1", llm)

        for _ in range(3):
            result = await tool.execute(name="base-gen2")
            assert result.success, result.error

        text = (p.prototypes_staging_dir() / "base-gen2" / "REVIEW.md").read_text(encoding="utf-8")
        assert text.count("# 评审记录") == 1
        assert text.count("r-1（fake/reviewer-model）：不通过") == 3
        assert text.count("理由甲。") == 3

    async def test_approve_carries_review_file(self, tmp_path: Path) -> None:
        """approve 的整目录 move 白拿 REVIEW.md：评审记录随提案晋级 trusted。"""
        p = TlonPaths(home=tmp_path)
        _stage_child(p)
        tool = _make_tool(p, "r-1", _FakeLLM("通过\n可以晋级。"))
        await tool.execute(name="base-gen2")

        approve_prototype("base-gen2", tlon_paths=p)

        trusted_review = p.prototypes_dir() / "base-gen2" / "REVIEW.md"
        assert trusted_review.exists()
        assert "r-1" in trusted_review.read_text(encoding="utf-8")
        assert not (p.prototypes_staging_dir() / "base-gen2").exists()
        # trusted 侧详情同样带 reviews
        _info, _behavior, _lessons, reviews = get_prototype_detail("base-gen2", tlon_paths=p)
        assert reviews is not None and "可以晋级。" in reviews

    @pytest.mark.parametrize("verdict", ["通过", "不通过", "弃权"])
    async def test_verdict_values(self, tmp_path: Path, verdict: str) -> None:
        """三个合法 verdict 标记都解析成功并落盘。"""
        p = TlonPaths(home=tmp_path)
        write_staging_prototype("staged", _BASE_SPEC, author_id="a", tlon_paths=p)
        tool = _make_tool(p, "r-1", _FakeLLM(f"{verdict}\n理由。"))

        result = await tool.execute(name="staged")

        assert result.success, result.error
        assert result.data["verdict"] == verdict

    async def test_illegal_verdict_line_rejected(self, tmp_path: Path) -> None:
        """首行不是三值之一 → success=False，不写 REVIEW.md。"""
        p = TlonPaths(home=tmp_path)
        write_staging_prototype("staged", _BASE_SPEC, author_id="a", tlon_paths=p)
        tool = _make_tool(p, "r-1", _FakeLLM("我觉得可以\n因为……"))

        result = await tool.execute(name="staged")

        assert not result.success
        assert "verdict" in result.error
        assert not (p.prototypes_staging_dir() / "staged" / "REVIEW.md").exists()

    async def test_missing_proposal_fails_before_llm(self, tmp_path: Path) -> None:
        """提案不存在 → success=False，评审调用一次都不发生。"""
        p = TlonPaths(home=tmp_path)
        llm = _FakeLLM("通过\n理由。")
        tool = _make_tool(p, "r-1", llm)

        result = await tool.execute(name="ghost")

        assert not result.success
        assert "no prototype named 'ghost'" in result.error
        assert llm.calls == []

    async def test_trusted_prototype_not_reviewable(self, tmp_path: Path) -> None:
        """trusted 库原型不可评审（只评 staging 提案）。"""
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", _BASE_SPEC, tlon_paths=p)
        llm = _FakeLLM("通过\n理由。")
        tool = _make_tool(p, "r-1", llm)

        result = await tool.execute(name="hand")

        assert not result.success
        assert llm.calls == []
