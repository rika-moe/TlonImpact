"""Near-kin rejection (ADR 0014): proposals that are reskin-level
duplicates of an existing prototype (trusted library or staging area) are
turned away before they reach disk — ADR 0014's non-convergence selection
pressure.

``find_near_kin`` (agents/prototypes/near_kin.py) is pure stdlib text similarity
(``difflib.SequenceMatcher.ratio`` on normalized text: comment lines and
whitespace stripped); the two proposal tools (``writePrototype`` /
``proposeBehavior``) receive it as an injected callable, fail-open like
the solvency gate. The human direct-write channel is never gated.
"""

from pathlib import Path

from tlon.agents.prototypes.management import write_trusted_prototype
from tlon.agents.prototypes.near_kin import find_near_kin
from tlon.agents.prototypes.staging import write_staging_prototype
from tlon.base.llm_client import LLMResponse
from tlon.base.paths import TlonPaths
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.tools.agents.propose_behavior import ProposeBehaviorTool
from tlon.tools.agents.write_prototype import WritePrototypeTool

_EXISTING_SPEC = """\
; 既有原型：邮件转发器
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions [(forward {:to ["x@tlon.internal"]})]}]}
"""

# 近似文本：仅换了转发目标地址（换皮级雷同）。
_RESKIN_SPEC = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions [(forward {:to ["y@tlon.internal"]})]}]}
"""

# 实质差异：不同事件、不同谓词、不同动作。
_DISTINCT_SPEC = """\
{:version 1
 :rules [{:name "boot-task"
          :on :boot
          :actions [(start {:task "daily-digest"})]}]}
"""

#  冒烟案例的形态：scribe 底稿的代际微改（reflect 间隔 15→30）。
_PARENT_SPEC = """\
; 记录者：标准认知程序
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :when (or (addressed-to-me? msg) (cc-ed? msg))
          :loop {:context {:sections [(persona) (protocol) (knowledge) (skills) (mail-history 20)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :max-tokens 1000000
                 :reflect {:enabled true :interval-minutes 15}}}
         {:name "boot"
          :on :boot
          :actions []}]}
"""

_TEMPLATE_SPEC = """\
{:version 1
 :parameters {:to {:type :string-list :required true}}
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ${to}})]}]}
"""


class TestFindNearKin:
    def test_identical_text_hits(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("base", _EXISTING_SPEC, tlon_paths=p)

        hit = find_near_kin(_EXISTING_SPEC, tlon_paths=p)

        assert hit == ("base", 1.0)

    def test_near_text_hits(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("base", _EXISTING_SPEC, tlon_paths=p)

        hit = find_near_kin(_RESKIN_SPEC, tlon_paths=p)

        assert hit is not None
        name, ratio = hit
        assert name == "base"
        assert ratio >= 0.9

    def test_staging_area_is_searched_too(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)
        write_staging_prototype("staged", _EXISTING_SPEC, tlon_paths=p)

        hit = find_near_kin(_EXISTING_SPEC, tlon_paths=p)

        assert hit == ("staged", 1.0)

    def test_most_similar_of_both_areas_wins(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("far", _DISTINCT_SPEC, tlon_paths=p)
        write_staging_prototype("near", _EXISTING_SPEC, tlon_paths=p)

        hit = find_near_kin(_RESKIN_SPEC, tlon_paths=p)

        assert hit is not None
        assert hit[0] == "near"

    def test_distinct_text_misses(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("base", _EXISTING_SPEC, tlon_paths=p)

        assert find_near_kin(_DISTINCT_SPEC, tlon_paths=p) is None

    def test_empty_library_misses(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)

        assert find_near_kin(_EXISTING_SPEC, tlon_paths=p) is None

    def test_template_prototype_compares_verbatim(self, tmp_path: Path) -> None:
        """模板原型（含 ${var} 占位符）不比挂：文本比较不做 EDN 解析。"""
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("relay", _TEMPLATE_SPEC, tlon_paths=p)

        assert find_near_kin(_TEMPLATE_SPEC, tlon_paths=p) == ("relay", 1.0)
        assert find_near_kin(_DISTINCT_SPEC, tlon_paths=p) is None

    def test_whitespace_and_comments_normalized_away(self, tmp_path: Path) -> None:
        """重排版（去注释、改缩进）不逃脱闸：规范化后仍是同一文本。"""
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("base", _EXISTING_SPEC, tlon_paths=p)
        reformatted = (
            '{:version 1 :rules [{:name "respond" :on :mail-delivered '
            ':actions [(forward {:to ["x@tlon.internal"]})]}]}\n'
        )

        hit = find_near_kin(reformatted, tlon_paths=p)

        assert hit == ("base", 1.0)

    def test_threshold_boundary(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("base", _EXISTING_SPEC, tlon_paths=p)
        # 一次只换一个字符的文本相似度约 0.99：阈值 0.99 之上不命中。
        assert find_near_kin(_RESKIN_SPEC, threshold=0.999, tlon_paths=p) is None

    def test_generational_micro_tweak_released_by_parent_exemption(self, tmp_path: Path) -> None:
        """边界实测（docs/design/prototypes.md 冒烟案例形态）：reflect
        间隔 15→30 的代际微改。

        真实数据：内置 scribe 原型的 behavior.tlon 仅把
        ``:interval-minutes 15`` 改为 ``30``，规范化文本的
        SequenceMatcher ratio = 0.996（本测试内联同形态文本实测
        0.995）。父本豁免（NEAT 式物种形成保护：创新在自己血脉内被
        保护几代）后该微改放行——世代通道的本质就是沿血脉微进化，
        阈值 0.9 拦的是换皮与跨血脉趋同。合理的代际改款（换谓词/
        动作/规则的实质重写）相似度远低于 0.9，本来就不受影响
        （见 test_distinct_text_misses，实测约 0.29）。
        """
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("parent", _PARENT_SPEC, tlon_paths=p)
        micro_tweak = _PARENT_SPEC.replace(":interval-minutes 15", ":interval-minutes 30")

        assert find_near_kin(micro_tweak, "parent", tlon_paths=p) is None

    def test_cross_bloodline_convergence_still_caught(self, tmp_path: Path) -> None:
        """同样的 15→30 微改文本，父本另有其人（或无父）→ 仍被拦：
        跨血脉趋同命中最近的近亲。"""
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("parent", _PARENT_SPEC, tlon_paths=p)
        write_trusted_prototype("unrelated", _DISTINCT_SPEC, tlon_paths=p)
        micro_tweak = _PARENT_SPEC.replace(":interval-minutes 15", ":interval-minutes 30")

        hit = find_near_kin(micro_tweak, "unrelated", tlon_paths=p)

        assert hit is not None
        name, ratio = hit
        assert name == "parent"
        assert ratio > 0.99  # 实测锚点：真实 scribe 底稿为 0.996

    def test_exclude_only_skips_the_named_prototype(self, tmp_path: Path) -> None:
        """豁免按名字精确跳过：豁免父本后，与另一个原型雷同仍命中。"""
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("parent", _PARENT_SPEC, tlon_paths=p)
        write_trusted_prototype("base", _EXISTING_SPEC, tlon_paths=p)

        hit = find_near_kin(_RESKIN_SPEC, "parent", tlon_paths=p)

        assert hit is not None
        assert hit[0] == "base"


class TestWritePrototypeNearKin:
    """writePrototype 的近亲闸：跨血脉命中 → success=False 且
    staging 无残留；父本豁免（显式 parent > 自身 prototype）→ 放行；
    检查异常 → fail-open 放行。"""

    def _make_tool(
        self,
        p: TlonPaths,
        prototype: str | None = None,
        near_kin: object = "REAL",
    ) -> WritePrototypeTool:
        if near_kin == "REAL":
            near_kin = lambda text, exclude: find_near_kin(  # noqa: E731
                text, exclude, tlon_paths=p
            )
        return WritePrototypeTool(
            author_id="adam",
            writer=lambda *a, **kw: write_staging_prototype(*a, tlon_paths=p, **kw),
            prototype=prototype,
            near_kin=near_kin,  # type: ignore[arg-type]
        )

    async def test_cross_bloodline_hit_rejected_nothing_on_disk(self, tmp_path: Path) -> None:
        """无父提案（新根）与既有原型换皮级雷同 → 被拦。"""
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("base", _EXISTING_SPEC, tlon_paths=p)
        tool = self._make_tool(p)

        result = await tool.execute(name="clone", behavior=_RESKIN_SPEC)

        assert not result.success
        assert "base" in result.error
        assert "base" in result.message
        assert not (p.prototypes_staging_dir() / "clone").exists()

    async def test_own_parent_exempted(self, tmp_path: Path) -> None:
        """作者的 prototype 即父本（未给显式 parent）：与父本换皮级
        相似的代际微改 → 豁免放行。"""
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("base", _EXISTING_SPEC, tlon_paths=p)
        tool = self._make_tool(p, prototype="base")

        result = await tool.execute(name="base-gen2", behavior=_RESKIN_SPEC)

        assert result.success, result.error

    async def test_explicit_parent_takes_priority_over_own_prototype(self, tmp_path: Path) -> None:
        """显式 parent > 自身 prototype：作者血脉是 base，但显式声明
        parent=other → 豁免 other 而非 base，与 base 雷同仍被拦。"""
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("base", _EXISTING_SPEC, tlon_paths=p)
        write_trusted_prototype("other", _DISTINCT_SPEC, tlon_paths=p)
        tool = self._make_tool(p, prototype="base")

        result = await tool.execute(name="clone", behavior=_RESKIN_SPEC, parent="other")

        assert not result.success
        assert "base" in result.error
        assert not (p.prototypes_staging_dir() / "clone").exists()

    async def test_near_kin_failure_is_fail_open(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)

        def _boom(text: str, exclude: str | None) -> tuple[str, float] | None:
            raise OSError("similarity scan broke")

        tool = self._make_tool(p, near_kin=_boom)

        result = await tool.execute(name="fine", behavior=_DISTINCT_SPEC)

        assert result.success, result.error

    async def test_no_provider_no_check(self, tmp_path: Path) -> None:
        """near_kin=None（未注入）→ 不查， identical 文本也照常落盘。"""
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("base", _EXISTING_SPEC, tlon_paths=p)
        tool = self._make_tool(p, near_kin=None)

        result = await tool.execute(name="clone", behavior=_EXISTING_SPEC)

        assert result.success, result.error


class _FakeLLM:
    """Duck-typed LLMClient returning one canned response."""

    def __init__(self, content: str) -> None:
        self._content = content

    async def chat(self, **kwargs: object) -> LLMResponse:
        return LLMResponse(content=self._content)


class TestProposeBehaviorNearKin:
    """proposeBehavior 的近亲闸：闸排在起草之后（被比较的是
    草稿文本）；父本恒为成员自己的 prototype，豁免后沿血脉微改放行，
    跨血脉命中 → success=False 且 staging 无残留；fail-open。"""

    def _make_tool(
        self,
        p: TlonPaths,
        agent_id: str,
        draft: str,
        prototype: str | None = "base",
        near_kin: object = "REAL",
    ) -> ProposeBehaviorTool:
        from tlon.agents.session_journal import SessionJournal

        if near_kin == "REAL":
            near_kin = lambda text, exclude: find_near_kin(  # noqa: E731
                text, exclude, tlon_paths=p
            )
        return ProposeBehaviorTool(
            agent_id=agent_id,
            prototype=prototype,
            author_id=agent_id,
            llm_client=_FakeLLM(draft),  # type: ignore[arg-type]
            memory=MemoryManager(agent_id, MemoryConfig(), data_dir=p.agent_dir(agent_id)),
            logs=SessionJournal(agent_id, p),
            writer=lambda *a, **kw: write_staging_prototype(*a, tlon_paths=p, **kw),
            tlon_paths=p,
            near_kin=near_kin,  # type: ignore[arg-type]
        )

    def _write_behavior(self, p: TlonPaths, agent_id: str) -> None:
        behavior_file = p.agent_behavior_file(agent_id)
        behavior_file.parent.mkdir(parents=True, exist_ok=True)
        behavior_file.write_text(_PARENT_SPEC, encoding="utf-8")

    async def test_parent_bloodline_micro_tweak_released(self, tmp_path: Path) -> None:
        """草稿与父本（成员自己的 prototype）换皮级相似 → 父本豁免，
        沿血脉的代际微改放行落盘。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        write_trusted_prototype("base", _EXISTING_SPEC, tlon_paths=p)
        self._write_behavior(p, agent_id)
        tool = self._make_tool(p, agent_id, _RESKIN_SPEC)

        result = await tool.execute(intent="把转发目标换成 y", name="base-gen2")

        assert result.success, result.error
        assert (p.prototypes_staging_dir() / "base-gen2" / "behavior.tlon").exists()

    async def test_cross_bloodline_hit_rejected_nothing_on_disk(self, tmp_path: Path) -> None:
        """草稿与别人的血脉换皮级雷同 → 豁免自己的父本也救不了，被拦。"""
        p = TlonPaths(home=tmp_path)
        agent_id = "c-other-00000001"
        write_trusted_prototype("base", _EXISTING_SPEC, tlon_paths=p)
        write_trusted_prototype("other", _DISTINCT_SPEC, tlon_paths=p)
        self._write_behavior(p, agent_id)
        tool = self._make_tool(p, agent_id, _RESKIN_SPEC, prototype="other")

        result = await tool.execute(intent="换皮", name="clone")

        assert not result.success
        assert "base" in result.error
        assert not (p.prototypes_staging_dir() / "clone").exists()

    async def test_near_kin_failure_is_fail_open(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)
        agent_id = "c-base-00000001"
        write_trusted_prototype("base", _EXISTING_SPEC, tlon_paths=p)
        self._write_behavior(p, agent_id)

        def _boom(text: str, exclude: str | None) -> tuple[str, float] | None:
            raise OSError("similarity scan broke")

        tool = self._make_tool(p, agent_id, _DISTINCT_SPEC, near_kin=_boom)

        result = await tool.execute(intent="实质改款", name="gen2")

        assert result.success, result.error
