"""Prototype management domain functions (WebUI PROTOTYPES view).

``write_trusted_prototype`` / ``get_prototype_detail`` / ``update_prototype``
/ ``delete_prototype`` back the admin REST endpoints: human-authored
creation lands directly in the trusted library, behavior changes rerun the
full validation chain before anything touches disk.
"""

import pytest

from tlon.agents.prototypes.catalog import get_prototype_detail
from tlon.agents.prototypes.instantiate import instantiate
from tlon.agents.prototypes.management import (
    delete_prototype,
    update_prototype,
    write_trusted_prototype,
)
from tlon.agents.prototypes.staging import (
    append_staging_review,
    approve_prototype,
    write_staging_prototype,
)
from tlon.base.config import yaml_load_file
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

        info, behavior, lessons, _reviews = get_prototype_detail("hand", tlon_paths=p)

        assert lessons is None
        assert info.name == "hand"
        assert info.trusted is True
        assert behavior == VALID_BEHAVIOR

    def test_staging_detail(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_staging_prototype("staged", VALID_BEHAVIOR, author_id="adam", tlon_paths=p)

        info, _behavior, _lessons, _reviews = get_prototype_detail("staged", staging=True, tlon_paths=p)

        assert info.trusted is False
        assert info.author == "adam"

    def test_missing_raises(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="no prototype named 'nope'"):
            get_prototype_detail("nope", tlon_paths=p)
        with pytest.raises(PrototypeError, match="no prototype named 'nope'"):
            get_prototype_detail("nope", staging=True, tlon_paths=p)


class TestStagingReviewFile:
    """``REVIEW.md`` peer-review records (issue ): advisory, append-only,
    carried into the trusted library by approval's whole-directory move."""

    def test_detail_without_reviews_returns_none(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_staging_prototype("staged", VALID_BEHAVIOR, author_id="adam", tlon_paths=p)

        _info, _behavior, _lessons, reviews = get_prototype_detail(
            "staged", staging=True, tlon_paths=p
        )

        assert reviews is None

    def test_append_roundtrip_and_accumulate(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_staging_prototype("staged", VALID_BEHAVIOR, author_id="adam", tlon_paths=p)

        append_staging_review("staged", "## t1 — r-1（m-1）：通过\n\n理由甲\n", tlon_paths=p)
        append_staging_review("staged", "## t2 — r-2（m-2）：弃权\n\n理由乙\n", tlon_paths=p)

        text = (p.prototypes_staging_dir() / "staged" / "REVIEW.md").read_text(
            encoding="utf-8"
        )
        assert text.count("理由甲") == 1 and text.count("理由乙") == 1
        assert text.index("理由甲") < text.index("理由乙")
        _info, _behavior, _lessons, reviews = get_prototype_detail(
            "staged", staging=True, tlon_paths=p
        )
        assert reviews == text

    def test_append_missing_staging_raises(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="no staged prototype named 'nope'"):
            append_staging_review("nope", "entry\n", tlon_paths=p)


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

        _info, behavior, _, _reviews = get_prototype_detail("hand", tlon_paths=p)
        assert behavior == VALID_BEHAVIOR_V2

    def test_invalid_behavior_update_leaves_disk_untouched(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, description="orig", tlon_paths=p)

        with pytest.raises(PrototypeError, match="validation failed"):
            update_prototype(
                "hand", description="changed", behavior_edn=INVALID_BEHAVIOR, tlon_paths=p
            )

        # Neither the behavior file nor the metadata was written.
        _info, behavior, _, _reviews = get_prototype_detail("hand", tlon_paths=p)
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

        info, behavior, _, _reviews = get_prototype_detail("hand", tlon_paths=p)
        assert info.parent is None
        assert behavior == VALID_BEHAVIOR


class TestLessonsFile:
    """Prototype ``LESSONS.md`` — retirement lessons packaged with the
    prototype (issue )."""

    def test_staging_write_persists_lessons(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_staging_prototype(
            "staged", VALID_BEHAVIOR, author_id="adam", lessons="- 教训甲\n", tlon_paths=p
        )

        lessons_file = p.prototypes_staging_dir() / "staged" / "LESSONS.md"
        assert lessons_file.read_text(encoding="utf-8") == "- 教训甲\n"
        _info, _behavior, lessons, _reviews = get_prototype_detail(
            "staged", staging=True, tlon_paths=p
        )
        assert lessons == "- 教训甲\n"

    def test_trusted_write_persists_lessons(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, lessons="- 教训乙\n", tlon_paths=p)

        lessons_file = p.prototypes_dir() / "hand" / "LESSONS.md"
        assert lessons_file.read_text(encoding="utf-8") == "- 教训乙\n"
        _info, _behavior, lessons, _reviews = get_prototype_detail("hand", tlon_paths=p)
        assert lessons == "- 教训乙\n"

    def test_no_lessons_no_file(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, tlon_paths=p)

        assert not (p.prototypes_dir() / "hand" / "LESSONS.md").exists()
        _info, _behavior, lessons, _reviews = get_prototype_detail("hand", tlon_paths=p)
        assert lessons is None

    def test_update_writes_lessons(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, tlon_paths=p)

        update_prototype("hand", lessons="- 新教训\n", tlon_paths=p)

        lessons_file = p.prototypes_dir() / "hand" / "LESSONS.md"
        assert lessons_file.read_text(encoding="utf-8") == "- 新教训\n"

    def test_update_none_preserves_lessons(self, tmp_path):
        """None = keep (same semantics as parent/proposal_note)."""
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, lessons="- 保留我\n", tlon_paths=p)

        update_prototype("hand", description="只改描述", tlon_paths=p)

        lessons_file = p.prototypes_dir() / "hand" / "LESSONS.md"
        assert lessons_file.read_text(encoding="utf-8") == "- 保留我\n"

    def test_approve_carries_lessons_to_trusted(self, tmp_path):
        """Approval is a whole-directory move — LESSONS.md rides along."""
        p = TlonPaths(home=tmp_path)
        write_staging_prototype(
            "staged", VALID_BEHAVIOR, author_id="adam", lessons="- 随包\n", tlon_paths=p
        )

        approve_prototype("staged", tlon_paths=p)

        assert (p.prototypes_dir() / "staged" / "LESSONS.md").exists()
        assert not (p.prototypes_staging_dir() / "staged").exists()


class TestInstantiateModel:
    """``instantiate`` requires the caller's ``model`` (ADR 0016): model
    selection is deployment policy, not genotype — it lands in the
    member's agent.yaml as ``llm.model_name``; a spec's top-level
    ``:llm`` key is rejected by the grammar."""

    BEHAVIOR_WITH_LLM = """\
{:version 1
 :llm {:model "deepseek/deepseek-chat"}
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["x@tlon.internal"]})]}]}
"""

    def test_model_param_lands_in_agent_yaml(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, tlon_paths=p)

        cfg = instantiate("hand", "agent1", "新成员", model="openai/gpt-5", tlon_paths=p)

        assert cfg.llm is not None
        assert cfg.llm.model_name == "openai/gpt-5"
        on_disk = yaml_load_file(p.agent_config_file("agent1"))
        assert on_disk is not None and on_disk.llm is not None
        assert on_disk.llm.model_name == "openai/gpt-5"

    def test_spec_llm_declaration_rejected(self, tmp_path):
        """A residual top-level ``:llm`` key fails validation — even the
        trusted-prototype write path refuses it, so ``instantiate`` never
        sees one (ADR 0016: the key is gone from the grammar, a load
        error rather than silently ignored)."""
        p = TlonPaths(home=tmp_path)

        with pytest.raises(PrototypeError, match=":llm"):
            write_trusted_prototype("hand", self.BEHAVIOR_WITH_LLM, tlon_paths=p)

    def test_missing_model_raises(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, tlon_paths=p)

        with pytest.raises(PrototypeError, match="model is required"):
            instantiate("hand", "agent1", "新成员", model="", tlon_paths=p)


class TestInstantiateLessons:
    """``instantiate`` copies the prototype's LESSONS.md to the member's
    config dir (issue )."""

    def test_copies_lessons_next_to_behavior(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, lessons="- 前辈教训\n", tlon_paths=p)

        instantiate("hand", "agent1", "新成员", model="openai/gpt-5", tlon_paths=p)

        lessons_file = p.agent_lessons_file("agent1")
        assert lessons_file.read_text(encoding="utf-8") == "- 前辈教训\n"

    def test_no_lessons_no_file(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, tlon_paths=p)

        instantiate("hand", "agent1", "新成员", model="openai/gpt-5", tlon_paths=p)

        assert not (tmp_path / "agent1" / "config" / "LESSONS.md").exists()


class TestInstantiateExecutionLevel:
    """``instantiate`` projects the spec's ``:execution`` declaration once
    into agent.yaml's ``docker.level`` (constitution 1,
    docs/design/execution-device.md); without the section the config
    default ("L2") applies unchanged."""

    L1_BEHAVIOR = """\
{:version 1
 :execution {:level :L1}
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["x@tlon.internal"]})]}]}
"""

    def test_execution_section_projects_docker_level(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", self.L1_BEHAVIOR, tlon_paths=p)

        cfg = instantiate("hand", "agent1", "新成员", model="openai/gpt-5", tlon_paths=p)

        assert cfg.docker.level == "L1"
        on_disk = yaml_load_file(p.agent_config_file("agent1"))
        assert on_disk is not None
        assert on_disk.docker.level == "L1"

    def test_absent_section_keeps_default(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        write_trusted_prototype("hand", VALID_BEHAVIOR, tlon_paths=p)

        cfg = instantiate("hand", "agent1", "新成员", model="openai/gpt-5", tlon_paths=p)

        assert cfg.docker.level == "L2"


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
