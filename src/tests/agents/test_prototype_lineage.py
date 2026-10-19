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
from tlon.agents.prototypes.staging import approve_prototype, write_staging_prototype
from tlon.base.paths import TlonPaths
from tlon.tools.agents.write_prototype import WritePrototypeTool
from tlon.tools.prototypes.get_lineage import GetPrototypeLineageTool
from tlon.tools.prototypes.ports import PrototypeError

SPEC_V1 = '{:version 1\n :rules [{:name "respond"\n          :on :mail-delivered\n          :actions [(mail-forward {:mail_id (mail-field msg :id) :to ["x@tlon.internal"]})]}]}\n'
SPEC_V2 = '{:version 1\n :rules [{:name "respond"\n          :on :mail-delivered\n          :actions [(mail-forward {:mail_id (mail-field msg :id) :to ["y@tlon.internal"]})]}]}\n'
SPEC_V3 = '{:version 1\n :rules [{:name "respond"\n          :on :mail-delivered\n          :actions [(mail-forward {:mail_id (mail-field msg :id) :to ["z@tlon.internal"]})]}]}\n'


def _three_generations(p: TlonPaths) -> tuple[str, str, str]:
    """gen1 (trusted root) → gen2 (trusted) → gen3 (staging proposal)."""
    created_1 = write_trusted_prototype(SPEC_V1, tlon_paths=p, display_name="定址转发")
    created_2 = write_staging_prototype(
        SPEC_V2,
        author_id="adam",
        parent=created_1.name,
        proposal_note="换转发目标",
        tlon_paths=p,
        display_name="定址转发",
    )
    approve_prototype(created_2.name, tlon_paths=p)
    created_3 = write_staging_prototype(
        SPEC_V3,
        author_id="adam",
        parent=created_2.name,
        proposal_note="再换转发目标",
        tlon_paths=p,
        display_name="定址转发",
    )
    return created_1.name, created_2.name, created_3.name


class TestGetPrototypeLineage:
    def test_three_generation_chain_root_first(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        gen1, gen2, gen3 = _three_generations(p)
        chain = get_prototype_lineage(gen3, tlon_paths=p)
        assert [e["name"] for e in chain] == [gen1, gen2, gen3]
        assert [e["generation"] for e in chain] == [1, 2, 3]
        assert [e["parent"] for e in chain] == [None, gen1, gen2]
        assert [e["trusted"] for e in chain] == [True, True, False]
        assert chain[1]["proposal_note"] == "换转发目标"
        assert chain[2]["proposal_note"] == "再换转发目标"
        assert all(not e["broken"] for e in chain)

    def test_per_generation_diff(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        gen1, gen2, gen3 = _three_generations(p)
        chain = get_prototype_lineage(gen3, tlon_paths=p)
        assert chain[0]["diff_from_parent"] is None
        diff12 = chain[1]["diff_from_parent"]
        assert f"--- {gen1}/behavior.tlon" in diff12
        assert f"+++ {gen2}/behavior.tlon" in diff12
        assert (
            '-          :actions [(mail-forward {:mail_id (mail-field msg :id) :to ["x@tlon.internal"]})]}'
            in diff12
        )
        assert (
            '+          :actions [(mail-forward {:mail_id (mail-field msg :id) :to ["y@tlon.internal"]})]}'
            in diff12
        )
        diff23 = chain[2]["diff_from_parent"]
        assert "y@tlon.internal" in diff23 and "z@tlon.internal" in diff23

    def test_single_root(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(SPEC_V1, tlon_paths=p, display_name="定址转发")
        chain = get_prototype_lineage(created_1.name, tlon_paths=p)
        assert len(chain) == 1
        assert chain[0]["generation"] == 1
        assert chain[0]["diff_from_parent"] is None
        assert chain[0]["broken"] is False

    def test_broken_chain_when_parent_deleted(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        gen1, gen2, gen3 = _three_generations(p)
        delete_prototype(gen2, tlon_paths=p)
        chain = get_prototype_lineage(gen3, tlon_paths=p)
        assert [e["name"] for e in chain] == [gen3]
        assert chain[0]["broken"] is True
        assert chain[0]["diff_from_parent"] is None

    def test_cycle_guard(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(SPEC_V1, tlon_paths=p, display_name="定址转发")
        created_2 = write_trusted_prototype(
            SPEC_V2, parent=created_1.name, tlon_paths=p, display_name="定址转发"
        )
        update_prototype(created_1.name, parent=created_2.name, tlon_paths=p)
        chain = get_prototype_lineage(created_1.name, tlon_paths=p)
        assert [e["name"] for e in chain] == [created_2.name, created_1.name]
        assert chain[0]["broken"] is True

    def test_unknown_name_raises(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="no prototype named 'nope'"):
            get_prototype_lineage("nope", tlon_paths=p)


class TestGetPrototypeLineageTool:
    async def test_returns_chain_with_diffs(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        gen1, gen2, gen3 = _three_generations(p)
        tool = GetPrototypeLineageTool(lineage=lambda name: get_prototype_lineage(name, p))
        result = await tool.execute(name=gen3)
        assert result.success
        lineage = result.data["lineage"]
        assert [e["name"] for e in lineage] == [gen1, gen2, gen3]
        assert lineage[2]["proposal_note"] == "再换转发目标"
        assert f"+++ {gen3}/behavior.tlon" in lineage[2]["diff_from_parent"]

    async def test_unknown_prototype_fails(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        tool = GetPrototypeLineageTool(lineage=lambda name: get_prototype_lineage(name, p))
        result = await tool.execute(name="ghost")
        assert not result.success
        assert "ghost" in (result.error or "")

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
        created_1 = write_trusted_prototype(SPEC_V1, tlon_paths=p, display_name="定址转发")
        tool = WritePrototypeTool(
            author_id="adam",
            writer=lambda *a, **kw: write_staging_prototype(*a, tlon_paths=p, **kw),
        )
        result = await tool.execute(
            behavior=SPEC_V2,
            parent=created_1.name,
            proposal_note="换转发目标",
            display_name="定址转发",
        )
        assert result.success
        assert result.data["parent"] == created_1.name
        assert result.data["generation"] == 2
        chain = get_prototype_lineage(result.data["name"], tlon_paths=p)
        assert [e["name"] for e in chain] == [created_1.name, result.data["name"]]
        assert chain[1]["proposal_note"] == "换转发目标"

    async def test_unknown_parent_returns_failure(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        tool = WritePrototypeTool(
            author_id="adam",
            writer=lambda *a, **kw: write_staging_prototype(*a, tlon_paths=p, **kw),
        )
        result = await tool.execute(behavior=SPEC_V2, parent="ghost", display_name="定址转发")
        assert not result.success
        assert "ghost" in (result.error or "")
