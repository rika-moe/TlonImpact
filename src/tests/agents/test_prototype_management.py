"""Prototype management domain functions (WebUI PROTOTYPES view).

``write_trusted_prototype`` / ``get_prototype_detail`` / ``update_prototype``
/ ``delete_prototype`` back the admin REST endpoints: human-authored
creation lands directly in the trusted library, behavior changes rerun the
full validation chain before anything touches disk.
"""

import pytest

from tlon.agents.prototypes import (
    approve_prototype,
    delete_prototype,
    get_prototype_detail,
    update_prototype,
    write_staging_prototype,
    write_trusted_prototype,
)
from tlon.base.paths import TlonPaths
from tlon.tools.prototypes.ports import PrototypeError

VALID_BEHAVIOR = """\
{:version 1
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["x@tlon.internal"]})]}]}
"""

VALID_BEHAVIOR_V2 = """\
{:version 1
 :rules [{:name "relay-v2"
          :on :mail-delivered
          :actions [(forward {:to ["y@tlon.internal"]})]}]}
"""

INVALID_BEHAVIOR = """\
{:version 1
 :rules [{:name "wild"
          :on :mail-delivered
          :actions [(jump {:to ["x@tlon.internal"]})]}]}
"""


class TestWriteTrustedPrototype:
    def test_creates_trusted_without_author(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        info = write_trusted_prototype(
            "hand", VALID_BEHAVIOR, display_name="手写", description="d", tlon_paths=p
        )

        assert info.name == "hand"
        assert info.display_name == "手写"
        assert info.description == "d"
        assert info.trusted is True
        assert info.author is None
        proto_dir = p.prototypes_dir() / "hand"
        assert (proto_dir / "behavior.tlon").read_text(encoding="utf-8") == VALID_BEHAVIOR
        metadata = (proto_dir / "prototype.edn").read_text(encoding="utf-8")
        assert ":trusted true" in metadata
        assert ":author" not in metadata

    def test_invalid_behavior_nothing_on_disk(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="validation failed"):
            write_trusted_prototype("bad", INVALID_BEHAVIOR, tlon_paths=p)
        assert not (p.prototypes_dir() / "bad").exists()

    def test_name_conflict_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("dup", VALID_BEHAVIOR, tlon_paths=p)
        with pytest.raises(PrototypeError, match="already exists in the trusted library"):
            write_trusted_prototype("dup", VALID_BEHAVIOR, tlon_paths=p)

    def test_staging_name_conflict_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_staging_prototype("dup", VALID_BEHAVIOR, author_id="adam", tlon_paths=p)
        with pytest.raises(PrototypeError, match="already exists in the staging area"):
            write_trusted_prototype("dup", VALID_BEHAVIOR, tlon_paths=p)

    def test_invalid_name_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="invalid prototype name"):
            write_trusted_prototype("a/b", VALID_BEHAVIOR, tlon_paths=p)


class TestGetPrototypeDetail:
    def test_trusted_detail_returns_metadata_and_behavior(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, description="d", tlon_paths=p)

        info, behavior = get_prototype_detail("hand", tlon_paths=p)

        assert info.name == "hand"
        assert info.trusted is True
        assert behavior == VALID_BEHAVIOR

    def test_staging_detail(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_staging_prototype("staged", VALID_BEHAVIOR, author_id="adam", tlon_paths=p)

        info, _behavior = get_prototype_detail("staged", staging=True, tlon_paths=p)

        assert info.trusted is False
        assert info.author == "adam"

    def test_missing_raises(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="no prototype named 'nope'"):
            get_prototype_detail("nope", tlon_paths=p)
        with pytest.raises(PrototypeError, match="no prototype named 'nope'"):
            get_prototype_detail("nope", staging=True, tlon_paths=p)


class TestUpdatePrototype:
    def test_metadata_update_preserves_trust_and_author(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_staging_prototype("staged", VALID_BEHAVIOR, author_id="adam", tlon_paths=p)

        info = update_prototype(
            "staged", display_name="新名字", description="新描述", staging=True, tlon_paths=p
        )

        assert info.display_name == "新名字"
        assert info.description == "新描述"
        assert info.trusted is False  # approval status untouched
        assert info.author == "adam"  # audit chain untouched
        behavior = (p.prototypes_staging_dir() / "staged" / "behavior.tlon").read_text(
            encoding="utf-8"
        )
        assert behavior == VALID_BEHAVIOR

    def test_behavior_update_rewrites_file(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, tlon_paths=p)

        update_prototype("hand", behavior_edn=VALID_BEHAVIOR_V2, tlon_paths=p)

        _info, behavior = get_prototype_detail("hand", tlon_paths=p)
        assert behavior == VALID_BEHAVIOR_V2

    def test_invalid_behavior_update_leaves_disk_untouched(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, description="orig", tlon_paths=p)

        with pytest.raises(PrototypeError, match="validation failed"):
            update_prototype(
                "hand", description="changed", behavior_edn=INVALID_BEHAVIOR, tlon_paths=p
            )

        # Neither the behavior file nor the metadata was written.
        _info, behavior = get_prototype_detail("hand", tlon_paths=p)
        assert behavior == VALID_BEHAVIOR
        metadata = (p.prototypes_dir() / "hand" / "prototype.edn").read_text(
            encoding="utf-8"
        )
        assert "orig" in metadata and "changed" not in metadata

    def test_nothing_to_update_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, tlon_paths=p)
        with pytest.raises(PrototypeError, match="nothing to update"):
            update_prototype("hand", tlon_paths=p)

    def test_missing_prototype_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="no prototype named 'nope'"):
            update_prototype("nope", description="d", tlon_paths=p)


class TestApprovePreservesLineage:
    """approve / update must keep the lineage fields (issue )."""

    def test_approve_keeps_parent_generation_note(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("base", VALID_BEHAVIOR, tlon_paths=p)
        write_staging_prototype(
            "child",
            VALID_BEHAVIOR_V2,
            author_id="adam",
            parent="base",
            proposal_note="改转发目标",
            tlon_paths=p,
        )

        info = approve_prototype("child", tlon_paths=p)

        assert info.trusted is True
        assert info.parent == "base"
        assert info.generation == 2
        assert info.proposal_note == "改转发目标"
        # On-disk metadata round-trips too.
        metadata = (p.prototypes_dir() / "child" / "prototype.edn").read_text(
            encoding="utf-8"
        )
        assert ":parent :base" in metadata
        assert ":generation 2" in metadata
        assert ":proposal-note" in metadata

    def test_update_keeps_lineage_fields(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("base", VALID_BEHAVIOR, tlon_paths=p)
        write_staging_prototype(
            "child",
            VALID_BEHAVIOR_V2,
            author_id="adam",
            parent="base",
            proposal_note="改转发目标",
            tlon_paths=p,
        )

        info = update_prototype("child", display_name="新名字", staging=True, tlon_paths=p)

        assert info.parent == "base"
        assert info.generation == 2
        assert info.proposal_note == "改转发目标"

    def test_update_can_relink_parent(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("base", VALID_BEHAVIOR, tlon_paths=p)
        write_trusted_prototype("base2", VALID_BEHAVIOR, parent="base", tlon_paths=p)
        write_staging_prototype("child", VALID_BEHAVIOR_V2, author_id="adam", tlon_paths=p)

        info = update_prototype(
            "child", parent="base2", proposal_note="重新挂到 base2", staging=True, tlon_paths=p
        )

        assert info.parent == "base2"
        assert info.generation == 3
        assert info.proposal_note == "重新挂到 base2"

    def test_update_unknown_parent_rejected_disk_untouched(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, tlon_paths=p)

        with pytest.raises(PrototypeError, match="parent prototype 'ghost' not found"):
            update_prototype("hand", parent="ghost", tlon_paths=p)

        info, behavior = get_prototype_detail("hand", tlon_paths=p)
        assert info.parent is None
        assert behavior == VALID_BEHAVIOR


class TestDeletePrototype:
    def test_deletes_trusted_directory(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, tlon_paths=p)

        delete_prototype("hand", tlon_paths=p)

        assert not (p.prototypes_dir() / "hand").exists()

    def test_missing_raises(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="no prototype named 'nope'"):
            delete_prototype("nope", tlon_paths=p)

    def test_path_traversal_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        for bad in ("..", "../x", "a/b", "a\\b"):
            with pytest.raises(PrototypeError):
                delete_prototype(bad, tlon_paths=p)
