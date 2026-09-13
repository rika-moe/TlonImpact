"""Staging-area write gate (stage 4a trust gate).

``write_staging_prototype`` validates before anything touches disk: EDN
parse + ``MemberSpec.from_edn`` + the single ``validate_member_spec``
validator (host-less registry — Adam's host predicates must not pass).
Also covers the ``listStagingPrototypes`` tool (): the staging
proposals an agent's digest can surface as pending human decisions.
"""

import pytest

from tlon.agents.prototypes import (
    list_staging_prototypes,
    write_staging_prototype,
    write_trusted_prototype,
)
from tlon.base.paths import TlonPaths
from tlon.tools.prototypes.list_staging import ListStagingPrototypesTool
from tlon.tools.prototypes.ports import PrototypeError


class TestWriteStagingValidation:
    def test_unknown_action_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="unknown action"):
            write_staging_prototype(
                "wild",
                """\
{:version 1
 :rules [{:name "wild"
          :on :mail-delivered
          :actions [(jump {:to ["x@tlon.internal"]})]}]}
""",
                tlon_paths=p,
            )
        # Nothing reached disk.
        assert not (p.prototypes_staging_dir() / "wild").exists()


VALID_SPEC = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :actions [(forward {:to ["x@tlon.internal"]})]}]}
"""


class TestListStagingPrototypesTool:
    """listStagingPrototypes (): untrusted proposals awaiting the
    human trust decision, listed with author for audit."""

    async def test_lists_staged_proposals(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_staging_prototype(
            "night-owl",
            VALID_SPEC,
            display_name="夜猫子",
            description="夜间值守",
            author_id="lin-zhiyao",
            tlon_paths=p,
        )
        tool = ListStagingPrototypesTool(lister=lambda: list_staging_prototypes(p))

        result = await tool.execute()

        assert result.success
        assert result.data == [
            {
                "name": "night-owl",
                "display_name": "夜猫子",
                "description": "夜间值守",
                "author": "lin-zhiyao",
                "parent": None,
                "generation": 1,
            }
        ]

    async def test_empty_staging(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        tool = ListStagingPrototypesTool(lister=lambda: list_staging_prototypes(p))

        result = await tool.execute()

        assert result.success
        assert result.data == []

    def test_tool_schema(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        tool = ListStagingPrototypesTool(lister=lambda: list_staging_prototypes(p))
        assert tool.name == "listStagingPrototypes"
        assert tool.parameters["required"] == []


class TestStagingLineageFields:
    """write_staging_prototype lineage fields (issue ): :parent links a
    proposal to the prototype it derives from; :generation is derived
    server-side as the parent's + 1."""

    def test_root_proposal_defaults(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        info = write_staging_prototype("root", VALID_SPEC, author_id="adam", tlon_paths=p)

        assert info.parent is None
        assert info.generation == 1
        assert info.proposal_note is None

    def test_child_derives_generation_from_trusted_parent(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("base", VALID_SPEC, tlon_paths=p)

        info = write_staging_prototype(
            "child",
            VALID_SPEC,
            author_id="adam",
            parent="base",
            proposal_note="换上更严格的门控",
            tlon_paths=p,
        )

        assert info.parent == "base"
        assert info.generation == 2
        assert info.proposal_note == "换上更严格的门控"

    def test_parent_may_live_in_staging(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_staging_prototype("gen1", VALID_SPEC, author_id="adam", tlon_paths=p)

        info = write_staging_prototype(
            "gen2", VALID_SPEC, author_id="adam", parent="gen1", tlon_paths=p
        )

        assert info.generation == 2

    def test_unknown_parent_rejected_nothing_on_disk(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="parent prototype 'ghost' not found"):
            write_staging_prototype(
                "orphan", VALID_SPEC, author_id="adam", parent="ghost", tlon_paths=p
            )
        assert not (p.prototypes_staging_dir() / "orphan").exists()

    def test_invalid_parent_name_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="invalid prototype name"):
            write_staging_prototype(
                "orphan", VALID_SPEC, author_id="adam", parent="../x", tlon_paths=p
            )
