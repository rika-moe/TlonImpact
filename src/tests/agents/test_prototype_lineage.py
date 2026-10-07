"""Prototype lineage (docs/design/prototypes.md): get_prototype_lineage
walks the :parent
chain, derives per-generation unified diffs of behavior.tlon, and marks
broken links (deleted/renamed parent) or cycles. Also covers the
``getPrototypeLineage`` tool."""

import pytest

from tlon.agents.prototypes.lineage import get_prototype_lineage
from tlon.agents.prototypes.management import (
    delete_prototype,
    update_prototype,
    write_trusted_prototype,
)
from tlon.agents.prototypes.staging import (
    approve_prototype,
    write_staging_prototype,
)
from tlon.base.paths import TlonPaths
from tlon.tools.agents.write_prototype import WritePrototypeTool
from tlon.tools.prototypes.get_lineage import GetPrototypeLineageTool
from tlon.tools.prototypes.ports import PrototypeError

SPEC_V1 = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions [(forward {:to ["x@tlon.internal"]})]}]}
"""

SPEC_V2 = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions [(forward {:to ["y@tlon.internal"]})]}]}
"""

SPEC_V3 = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions [(forward {:to ["z@tlon.internal"]})]}]}
"""


def _three_generations(p: TlonPaths) -> None:
    """gen1 (trusted root) → gen2 (trusted) → gen3 (staging proposal)."""
    write_trusted_prototype("gen1", SPEC_V1, tlon_paths=p)
    write_staging_prototype(
        "gen2",
        SPEC_V2,
        author_id="adam",
        parent="gen1",
        proposal_note="换转发目标",
        tlon_paths=p,
    )
    approve_prototype("gen2", tlon_paths=p)
    write_staging_prototype(
        "gen3",
        SPEC_V3,
        author_id="adam",
        parent="gen2",
        proposal_note="再换转发目标",
        tlon_paths=p,
    )


class TestGetPrototypeLineage:
    def test_three_generation_chain_root_first(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _three_generations(p)

        chain = get_prototype_lineage("gen3", tlon_paths=p)

        assert [e["name"] for e in chain] == ["gen1", "gen2", "gen3"]
        assert [e["generation"] for e in chain] == [1, 2, 3]
        assert [e["parent"] for e in chain] == [None, "gen1", "gen2"]
        assert [e["trusted"] for e in chain] == [True, True, False]
        assert chain[1]["proposal_note"] == "换转发目标"
        assert chain[2]["proposal_note"] == "再换转发目标"
        assert all(not e["broken"] for e in chain)

    def test_per_generation_diff(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _three_generations(p)

        chain = get_prototype_lineage("gen3", tlon_paths=p)

        assert chain[0]["diff_from_parent"] is None
        diff12 = chain[1]["diff_from_parent"]
        assert "--- gen1/behavior.tlon" in diff12
        assert "+++ gen2/behavior.tlon" in diff12
        assert '-          :actions [(forward {:to ["x@tlon.internal"]})]}' in diff12
        assert '+          :actions [(forward {:to ["y@tlon.internal"]})]}' in diff12
        diff23 = chain[2]["diff_from_parent"]
        assert "y@tlon.internal" in diff23 and "z@tlon.internal" in diff23

    def test_single_root(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("solo", SPEC_V1, tlon_paths=p)

        chain = get_prototype_lineage("solo", tlon_paths=p)

        assert len(chain) == 1
        assert chain[0]["generation"] == 1
        assert chain[0]["diff_from_parent"] is None
        assert chain[0]["broken"] is False

    def test_broken_chain_when_parent_deleted(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _three_generations(p)
        delete_prototype("gen2", tlon_paths=p)

        chain = get_prototype_lineage("gen3", tlon_paths=p)

        # The walk stops at gen3: its parent gen2 is gone.
        assert [e["name"] for e in chain] == ["gen3"]
        assert chain[0]["broken"] is True
        assert chain[0]["diff_from_parent"] is None

    def test_cycle_guard(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("a", SPEC_V1, tlon_paths=p)
        write_trusted_prototype("b", SPEC_V2, parent="a", tlon_paths=p)
        # Re-link the root onto its own descendant: a → b → a cycle.
        update_prototype("a", parent="b", tlon_paths=p)

        chain = get_prototype_lineage("a", tlon_paths=p)

        assert [e["name"] for e in chain] == ["b", "a"]
        assert chain[0]["broken"] is True  # b's parent a is already visited

    def test_unknown_name_raises(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="no prototype named 'nope'"):
            get_prototype_lineage("nope", tlon_paths=p)


class TestGetPrototypeLineageTool:
    async def test_returns_chain_with_diffs(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _three_generations(p)
        tool = GetPrototypeLineageTool(lineage=lambda name: get_prototype_lineage(name, p))

        result = await tool.execute(name="gen3")

        assert result.success
        lineage = result.data["lineage"]
        assert [e["name"] for e in lineage] == ["gen1", "gen2", "gen3"]
        assert lineage[2]["proposal_note"] == "再换转发目标"
        assert "+++ gen3/behavior.tlon" in lineage[2]["diff_from_parent"]

    async def test_unknown_prototype_fails(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        tool = GetPrototypeLineageTool(lineage=lambda name: get_prototype_lineage(name, p))

        result = await tool.execute(name="nope")

        assert not result.success
        assert "nope" in (result.error or "")

    def test_tool_schema(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        tool = GetPrototypeLineageTool(lineage=lambda name: get_prototype_lineage(name, p))
        assert tool.name == "getPrototypeLineage"
        assert tool.parameters["required"] == ["name"]


class TestWritePrototypeToolLineage:
    """writePrototype passes parent / proposal_note through to the staging
    writer (docs/design/prototypes.md)."""

    async def test_parent_and_note_passed_through(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("base", SPEC_V1, tlon_paths=p)
        tool = WritePrototypeTool(
            author_id="adam",
            writer=lambda *a, **kw: write_staging_prototype(*a, tlon_paths=p, **kw),
        )

        result = await tool.execute(
            name="child",
            behavior=SPEC_V2,
            parent="base",
            proposal_note="换转发目标",
        )

        assert result.success
        assert result.data["parent"] == "base"
        assert result.data["generation"] == 2

        chain = get_prototype_lineage("child", tlon_paths=p)
        assert [e["name"] for e in chain] == ["base", "child"]
        assert chain[1]["proposal_note"] == "换转发目标"

    async def test_unknown_parent_returns_failure(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        tool = WritePrototypeTool(
            author_id="adam",
            writer=lambda *a, **kw: write_staging_prototype(*a, tlon_paths=p, **kw),
        )

        result = await tool.execute(name="orphan", behavior=SPEC_V2, parent="ghost")

        assert not result.success
        assert "ghost" in (result.error or "")
