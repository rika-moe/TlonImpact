"""Near-kin rejection (ADR 0014): proposals that are reskin-level
duplicates of an existing prototype (trusted library or staging area) are
turned away before they reach disk — ADR 0014's non-convergence selection
pressure.

``find_near_kin`` (agents/prototypes/near_kin.py) is pure stdlib text similarity
(``difflib.SequenceMatcher.ratio`` on normalized text: comment lines and
whitespace stripped); the proposal tool ``prototype-write`` receives it as an injected callable, fail-open like
the solvency gate. The human direct-write channel is never gated.
"""

from pathlib import Path

from tests.evolution_helpers import make_write_tool
from tlon.agents.prototypes.management import write_trusted_prototype
from tlon.agents.prototypes.near_kin import find_near_kin
from tlon.agents.prototypes.staging import write_staging_prototype
from tlon.base.paths import TlonPaths
from tlon.evolution.write_prototype import bundle_text
from tlon.operations.adapters import OperationTool

_PROGRAM = "{:version 2 :handlers {:mail-delivered work.reply/handle}}"
_EXISTING_BUNDLE = {
    "behavior": _PROGRAM,
    "modules": {
        "work/helpers.tlon": "(ns work.helpers (:export identity)) (defn identity [value] value)",
        "work/reply.tlon": '(ns work.reply (:export handle)) (defn handle [event] (let [receipt (mail-forward {:mail_id (get (get event :mail) :id) :to ["x@tlon.internal"]})] {:result receipt}))',
    },
}
_RESKIN_BUNDLE = {
    "behavior": _PROGRAM,
    "modules": {
        path: source.replace('"x@tlon.internal"', '"y@tlon.internal"')
        for path, source in _EXISTING_BUNDLE["modules"].items()
    },
}
_DISTINCT_BUNDLE = {
    "behavior": _PROGRAM,
    "modules": {
        "work/helpers.tlon": "(ns work.helpers (:export identity)) (defn identity [value] value)",
        "work/reply.tlon": '(ns work.reply (:export handle)) (defn handle [event] {:result "offline pure calculation"})',
    },
}


class TestFindNearKin:
    def test_identical_and_reskinned_module_packages(self, tmp_path: Path) -> None:
        paths = TlonPaths(tmp_path)
        parent = write_trusted_prototype(
            _EXISTING_BUNDLE["behavior"],
            modules=_EXISTING_BUNDLE["modules"],
            tlon_paths=paths,
            display_name="转发",
        )
        assert find_near_kin(bundle_text(**_EXISTING_BUNDLE), tlon_paths=paths) == (
            parent.name,
            1.0,
        )
        found = find_near_kin(bundle_text(**_RESKIN_BUNDLE), tlon_paths=paths)
        assert found is not None and found[0] == parent.name and found[1] > 0.9
        assert (
            find_near_kin(bundle_text(**_RESKIN_BUNDLE), threshold=0.999, tlon_paths=paths) is None
        )

    def test_staging_is_searched_and_most_similar_wins(self, tmp_path: Path) -> None:
        paths = TlonPaths(tmp_path)
        write_trusted_prototype(
            _DISTINCT_BUNDLE["behavior"],
            modules=_DISTINCT_BUNDLE["modules"],
            tlon_paths=paths,
            display_name="计算",
        )
        staged = write_staging_prototype(
            _EXISTING_BUNDLE["behavior"],
            modules=_EXISTING_BUNDLE["modules"],
            tlon_paths=paths,
            display_name="转发",
        )
        found = find_near_kin(bundle_text(**_RESKIN_BUNDLE), tlon_paths=paths)
        assert found is not None and found[0] == staged.name

    def test_distinct_and_empty_library_miss(self, tmp_path: Path) -> None:
        paths = TlonPaths(tmp_path)
        assert find_near_kin(bundle_text(**_EXISTING_BUNDLE), tlon_paths=paths) is None
        write_trusted_prototype(
            _EXISTING_BUNDLE["behavior"],
            modules=_EXISTING_BUNDLE["modules"],
            tlon_paths=paths,
            display_name="转发",
        )
        assert find_near_kin(bundle_text(**_DISTINCT_BUNDLE), tlon_paths=paths) is None

    def test_whitespace_comments_and_parent_exemption(self, tmp_path: Path) -> None:
        paths = TlonPaths(tmp_path)
        parent = write_trusted_prototype(
            _EXISTING_BUNDLE["behavior"],
            modules=_EXISTING_BUNDLE["modules"],
            tlon_paths=paths,
            display_name="转发",
        )
        text = bundle_text(**_EXISTING_BUNDLE)
        reformatted = "; arbitrary comment\n" + text.replace(" ", "  ")
        assert find_near_kin(reformatted, tlon_paths=paths) == (parent.name, 1.0)
        assert find_near_kin(bundle_text(**_RESKIN_BUNDLE), parent.name, tlon_paths=paths) is None
        other = write_trusted_prototype(
            _DISTINCT_BUNDLE["behavior"],
            modules=_DISTINCT_BUNDLE["modules"],
            tlon_paths=paths,
            display_name="计算",
        )
        found = find_near_kin(bundle_text(**_RESKIN_BUNDLE), other.name, tlon_paths=paths)
        assert found is not None and found[0] == parent.name

    def test_template_modules_compare_verbatim(self, tmp_path: Path) -> None:
        paths = TlonPaths(tmp_path)
        behavior = "{:version 2 :parameters {:target {:type :string :required true}} :handlers {:mail-delivered work/handle}}"
        modules = {
            "work.tlon": "(ns work (:export handle)) (defn handle [event] {:result ${target}})"
        }
        parent = write_trusted_prototype(
            behavior, modules=modules, tlon_paths=paths, display_name="模板"
        )
        assert find_near_kin(bundle_text(behavior, modules), tlon_paths=paths) == (parent.name, 1.0)


class TestWritePrototypeNearKin:
    """prototype-write 的近亲闸：跨血脉命中 → success=False 且
    staging 无残留；父本豁免（显式 parent > 自身 prototype）→ 放行；
    检查异常 → fail-open 放行。"""

    def _make_tool(
        self, p: TlonPaths, prototype: str | None = None, near_kin: object = "REAL"
    ) -> OperationTool:
        if near_kin == "REAL":

            def near_kin(text: str, exclude: str | None) -> tuple[str, float] | None:
                return find_near_kin(text, exclude, tlon_paths=p)

        return make_write_tool(
            author_id="adam",
            writer=lambda *a, **kw: write_staging_prototype(*a, tlon_paths=p, **kw),
            prototype=prototype,
            near_kin=near_kin,
        )

    async def test_cross_bloodline_hit_rejected_nothing_on_disk(self, tmp_path: Path) -> None:
        """无父提案（新根）与既有原型换皮级雷同 → 被拦。"""
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            _EXISTING_BUNDLE["behavior"],
            modules=_EXISTING_BUNDLE["modules"],
            tlon_paths=p,
            display_name="定址转发",
        )
        tool = self._make_tool(p)
        result = await tool.execute_context({**_RESKIN_BUNDLE, "display_name": "定址转发"})
        assert not result.success
        assert created_1.name in result.error
        assert created_1.name in result.error
        assert not p.prototypes_staging_dir().exists()

    async def test_own_parent_exempted(self, tmp_path: Path) -> None:
        """作者的 prototype 即父本（未给显式 parent）：与父本换皮级
        相似的代际微改 → 豁免放行。"""
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            _EXISTING_BUNDLE["behavior"],
            modules=_EXISTING_BUNDLE["modules"],
            tlon_paths=p,
            display_name="定址转发",
        )
        tool = self._make_tool(p, prototype=created_1.name)
        result = await tool.execute_context({**_RESKIN_BUNDLE, "display_name": "定址转发"})
        assert result.success, result.error

    async def test_explicit_parent_takes_priority_over_own_prototype(self, tmp_path: Path) -> None:
        """显式 parent > 自身 prototype：作者血脉是 base，但显式声明
        parent=other → 豁免 other 而非 base，与 base 雷同仍被拦。"""
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            _EXISTING_BUNDLE["behavior"],
            modules=_EXISTING_BUNDLE["modules"],
            tlon_paths=p,
            display_name="定址转发",
        )
        created_2 = write_trusted_prototype(
            _DISTINCT_BUNDLE["behavior"],
            modules=_DISTINCT_BUNDLE["modules"],
            tlon_paths=p,
            display_name="定址转发",
        )
        tool = self._make_tool(p, prototype=created_1.name)
        result = await tool.execute_context(
            {**_RESKIN_BUNDLE, "parent": created_2.name, "display_name": "定址转发"}
        )
        assert not result.success
        assert created_1.name in result.error
        assert not p.prototypes_staging_dir().exists()

    async def test_near_kin_failure_is_fail_open(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)

        def _boom(text: str, exclude: str | None) -> tuple[str, float] | None:
            raise OSError("similarity scan broke")

        tool = self._make_tool(p, near_kin=_boom)
        result = await tool.execute_context({**_DISTINCT_BUNDLE, "display_name": "定址转发"})
        assert result.success, result.error

    async def test_no_provider_no_check(self, tmp_path: Path) -> None:
        """near_kin=None（未注入）→ 不查， identical 文本也照常落盘。"""
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype(
            _EXISTING_BUNDLE["behavior"],
            modules=_EXISTING_BUNDLE["modules"],
            tlon_paths=p,
            display_name="定址转发",
        )
        tool = self._make_tool(p, near_kin=None)
        result = await tool.execute_context({**_EXISTING_BUNDLE, "display_name": "定址转发"})
        assert result.success, result.error
