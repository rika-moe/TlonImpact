"""Prototype management domain functions (WebUI PROTOTYPES view).

``write_trusted_prototype`` / ``get_prototype_detail`` / ``update_prototype``
/ ``delete_prototype`` back the admin REST endpoints: human-authored
creation lands directly in the trusted library, behavior changes rerun the
full validation chain before anything touches disk.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from tlon.agents.prototypes.catalog import get_prototype_detail
from tlon.agents.prototypes.info import PrototypeInfo
from tlon.agents.prototypes.instantiate import instantiate
from tlon.agents.prototypes.management import (
    delete_prototype,
    update_prototype,
    write_trusted_prototype,
)
from tlon.agents.prototypes.reviews import read_prototype, submit_prototype_review
from tlon.agents.prototypes.staging import (
    approve_prototype,
    write_staging_prototype,
)
from tlon.base.config import yaml_load_file
from tlon.base.paths import TlonPaths
from tlon.evolution.ports import PrototypeError

_FIXTURE_MODULES = {
    "{:version 2 :handlers {:mail-delivered work.fixturee7336d73/handle0}}\n": {
        "work/fixturee7336d73.tlon": '(ns work.fixturee7336d73 (:export handle0))\n(defn handle0 [event] (let [msg (get event :mail) self (get event :self)] (let [action0 (mail-forward {:mail_id (mail-field msg :id) :to ["x@tlon.internal"]})] {:result nil})))\n'
    },
    "{:version 2 :handlers {:mail-delivered work.fixture7e2a3552/handle0}}\n": {
        "work/fixture7e2a3552.tlon": '(ns work.fixture7e2a3552 (:export handle0))\n(defn handle0 [event] (let [msg (get event :mail) self (get event :self)] (let [action0 (mail-forward {:mail_id (mail-field msg :id) :to ["y@tlon.internal"]})] {:result nil})))\n'
    },
    "{:version 2 :handlers {:mail-delivered work.fixture8abb1c6f/handle0}}\n": {
        "work/fixture8abb1c6f.tlon": '(ns work.fixture8abb1c6f (:export handle0))\n(defn handle0 [event] (let [msg (get event :mail) self (get event :self)] (let [action0 (jump {:to ["x@tlon.internal"]})] {:result nil})))\n'
    },
    '{:version 2 :handlers {:mail-delivered work.fixtured59e2318/handle0} :llm {:model "deepseek/deepseek-chat"}}\n': {
        "work/fixtured59e2318.tlon": '(ns work.fixtured59e2318 (:export handle0))\n(defn handle0 [event] (let [msg (get event :mail) self (get event :self)] (let [action0 (mail-forward {:mail_id (mail-field msg :id) :to ["x@tlon.internal"]})] {:result nil})))\n'
    },
    "{:version 2 :handlers {:mail-delivered work.fixturec79a4a08/handle0} :execution {:level :L1}}\n": {
        "work/fixturec79a4a08.tlon": '(ns work.fixturec79a4a08 (:export handle0))\n(defn handle0 [event] (let [msg (get event :mail) self (get event :self)] (let [action0 (mail-forward {:mail_id (mail-field msg :id) :to ["x@tlon.internal"]})] {:result nil})))\n'
    },
}


VALID_BEHAVIOR = "{:version 2 :handlers {:mail-delivered work.fixturee7336d73/handle0}}\n"
VALID_BEHAVIOR_V2 = "{:version 2 :handlers {:mail-delivered work.fixture7e2a3552/handle0}}\n"
INVALID_BEHAVIOR = "{:version 2 :handlers {:mail-delivered work.fixture8abb1c6f/handle0}}\n"


class TestWriteTrustedPrototype:
    def test_creates_trusted_without_author(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        info = write_trusted_prototype(
            VALID_BEHAVIOR,
            display_name="手写",
            description="d",
            tlon_paths=p,
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        assert info.name == info.name
        assert info.display_name == "手写"
        assert info.description == "d"
        assert info.trusted is True
        assert info.author is None
        proto_dir = p.prototypes_dir() / info.name
        assert (proto_dir / "behavior.tlon").read_text(encoding="utf-8") == VALID_BEHAVIOR
        metadata = (proto_dir / "prototype.tlon").read_text(encoding="utf-8")
        assert ":trusted true" in metadata
        assert ":author" not in metadata

    def test_invalid_behavior_nothing_on_disk(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="validation failed"):
            write_trusted_prototype(
                INVALID_BEHAVIOR,
                tlon_paths=p,
                display_name="定址转发",
                modules=_FIXTURE_MODULES.get(INVALID_BEHAVIOR),
            )
        assert not p.prototypes_dir().exists()

    def test_name_conflict_rejected(self, tmp_path, monkeypatch):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        monkeypatch.setattr(
            "tlon.agents.prototypes.management.new_prototype_id", lambda: created_1.name
        )
        with pytest.raises(PrototypeError, match="already exists"):
            write_trusted_prototype(
                VALID_BEHAVIOR,
                tlon_paths=p,
                display_name="定址转发",
                modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
            )

    def test_staging_name_conflict_rejected(self, tmp_path, monkeypatch):
        p = TlonPaths(home=tmp_path)
        created_1 = write_staging_prototype(
            VALID_BEHAVIOR,
            author_id="adam",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        monkeypatch.setattr(
            "tlon.agents.prototypes.management.new_prototype_id", lambda: created_1.name
        )
        with pytest.raises(PrototypeError, match="already exists"):
            write_trusted_prototype(
                VALID_BEHAVIOR,
                tlon_paths=p,
                display_name="定址转发",
                modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
            )

    def test_empty_behavior_label_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="display_name"):
            write_trusted_prototype(
                VALID_BEHAVIOR,
                tlon_paths=p,
                display_name="  ",
                modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
            )


class TestGetPrototypeDetail:
    def test_trusted_detail_returns_metadata_and_behavior(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            description="d",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        info, behavior, lessons, _reviews = get_prototype_detail(created_1.name, tlon_paths=p)
        assert lessons is None
        assert info.name == created_1.name
        assert info.trusted is True
        assert behavior == VALID_BEHAVIOR

    def test_staging_detail(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_staging_prototype(
            VALID_BEHAVIOR,
            author_id="adam",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        info, _behavior, _lessons, _reviews = get_prototype_detail(
            created_1.name, staging=True, tlon_paths=p
        )
        assert info.trusted is False
        assert info.author == "adam"

    def test_missing_raises(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="no prototype named 'nope'"):
            get_prototype_detail("nope", tlon_paths=p)
        with pytest.raises(PrototypeError, match="no prototype named 'nope'"):
            get_prototype_detail("nope", staging=True, tlon_paths=p)


class TestStagingReviewFile:
    """``REVIEW.md`` peer-review records (docs/design/prototypes.md):
    advisory, append-only,
    carried into the trusted library by approval's whole-directory move."""

    def test_detail_without_reviews_returns_none(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_staging_prototype(
            VALID_BEHAVIOR,
            author_id="adam",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        _info, _behavior, _lessons, reviews = get_prototype_detail(
            created_1.name, staging=True, tlon_paths=p
        )
        assert reviews is None

    def test_append_roundtrip_and_accumulate(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_staging_prototype(
            VALID_BEHAVIOR,
            author_id="adam",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        submit_prototype_review(
            created_1.name,
            read_prototype(created_1.name, tlon_paths=p)["digest"],
            "通过",
            "理由甲",
            reviewer_id="r-1",
            run_id="run",
            call_id="call-1",
            tlon_paths=p,
        )
        submit_prototype_review(
            created_1.name,
            read_prototype(created_1.name, tlon_paths=p)["digest"],
            "弃权",
            "理由乙",
            reviewer_id="r-2",
            run_id="run",
            call_id="call-2",
            tlon_paths=p,
        )
        text = (p.prototypes_staging_dir() / created_1.name / "REVIEW.md").read_text(
            encoding="utf-8"
        )
        assert "理由甲" in text and "理由乙" in text
        assert text.index("理由甲") < text.index("理由乙")
        _info, _behavior, _lessons, reviews = get_prototype_detail(
            created_1.name, staging=True, tlon_paths=p
        )
        assert reviews == text

    def test_append_missing_staging_raises(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="no prototype named 'nope'"):
            submit_prototype_review(
                "nope",
                "digest",
                "通过",
                "理由",
                reviewer_id="r",
                run_id="run",
                call_id="call",
                tlon_paths=p,
            )


class TestUpdatePrototype:
    def test_metadata_update_preserves_trust_and_author(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_staging_prototype(
            VALID_BEHAVIOR,
            author_id="adam",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        info = update_prototype(
            created_1.name, display_name="新名字", description="新描述", staging=True, tlon_paths=p
        )
        assert info.display_name == "新名字"
        assert info.description == "新描述"
        assert info.trusted is False
        assert info.author == "adam"
        behavior = (p.prototypes_staging_dir() / created_1.name / "behavior.tlon").read_text(
            encoding="utf-8"
        )
        assert behavior == VALID_BEHAVIOR

    def test_behavior_update_rewrites_file(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        with pytest.raises(PrototypeError, match="immutable"):
            update_prototype(
                created_1.name,
                behavior_edn=VALID_BEHAVIOR_V2,
                tlon_paths=p,
                modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR_V2),
            )
        _info, behavior, _, _reviews = get_prototype_detail(created_1.name, tlon_paths=p)
        assert behavior == VALID_BEHAVIOR

    def test_invalid_behavior_update_leaves_disk_untouched(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            description="orig",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        with pytest.raises(PrototypeError, match="validation failed"):
            update_prototype(
                created_1.name,
                description="changed",
                behavior_edn=INVALID_BEHAVIOR,
                tlon_paths=p,
                modules=_FIXTURE_MODULES.get(INVALID_BEHAVIOR),
            )
        _info, behavior, _, _reviews = get_prototype_detail(created_1.name, tlon_paths=p)
        assert behavior == VALID_BEHAVIOR
        metadata = (p.prototypes_dir() / created_1.name / "prototype.tlon").read_text(
            encoding="utf-8"
        )
        assert "orig" in metadata and "changed" not in metadata

    def test_nothing_to_update_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        with pytest.raises(PrototypeError, match="nothing to update"):
            update_prototype(created_1.name, tlon_paths=p)

    def test_missing_prototype_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="no prototype named 'nope'"):
            update_prototype("nope", description="d", tlon_paths=p)


class TestApprovePreservesLineage:
    """approve / update must keep the lineage fields
    (docs/design/prototypes.md)."""

    def test_approve_keeps_parent_generation_note(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        created_2 = write_staging_prototype(
            VALID_BEHAVIOR_V2,
            author_id="adam",
            parent=created_1.name,
            proposal_note="改转发目标",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR_V2),
        )
        info = approve_prototype(created_2.name, tlon_paths=p)
        assert info.trusted is True
        assert info.parent == created_1.name
        assert info.generation == 2
        assert info.proposal_note == "改转发目标"
        metadata = (p.prototypes_dir() / created_2.name / "prototype.tlon").read_text(
            encoding="utf-8"
        )
        assert f":parent :{created_1.name}" in metadata
        assert ":generation 2" in metadata
        assert ":proposal-note" in metadata

    def test_update_keeps_lineage_fields(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        created_2 = write_staging_prototype(
            VALID_BEHAVIOR_V2,
            author_id="adam",
            parent=created_1.name,
            proposal_note="改转发目标",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR_V2),
        )
        info = update_prototype(created_2.name, display_name="新名字", staging=True, tlon_paths=p)
        assert info.parent == created_1.name
        assert info.generation == 2
        assert info.proposal_note == "改转发目标"

    def test_update_can_relink_parent(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        created_2 = write_trusted_prototype(
            VALID_BEHAVIOR,
            parent=created_1.name,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        created_3 = write_staging_prototype(
            VALID_BEHAVIOR_V2,
            author_id="adam",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR_V2),
        )
        info = update_prototype(
            created_3.name,
            parent=created_2.name,
            proposal_note="重新挂到 base2",
            staging=True,
            tlon_paths=p,
        )
        assert info.parent == created_2.name
        assert info.generation == 3
        assert info.proposal_note == "重新挂到 base2"

    def test_update_unknown_parent_rejected_disk_untouched(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        with pytest.raises(PrototypeError, match="parent prototype 'ghost' not found"):
            update_prototype(created_1.name, parent="ghost", tlon_paths=p)
        info, behavior, _, _reviews = get_prototype_detail(created_1.name, tlon_paths=p)
        assert info.parent is None
        assert behavior == VALID_BEHAVIOR


class TestLessonsFile:
    """Prototype ``LESSONS.md`` — retirement lessons packaged with the
    prototype (docs/design/prototypes.md)."""

    def test_staging_write_persists_lessons(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_staging_prototype(
            VALID_BEHAVIOR,
            author_id="adam",
            lessons="- 教训甲\n",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        lessons_file = p.prototypes_staging_dir() / created_1.name / "LESSONS.md"
        assert lessons_file.read_text(encoding="utf-8") == "- 教训甲\n"
        _info, _behavior, lessons, _reviews = get_prototype_detail(
            created_1.name, staging=True, tlon_paths=p
        )
        assert lessons == "- 教训甲\n"

    def test_trusted_write_persists_lessons(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            lessons="- 教训乙\n",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        lessons_file = p.prototypes_dir() / created_1.name / "LESSONS.md"
        assert lessons_file.read_text(encoding="utf-8") == "- 教训乙\n"
        _info, _behavior, lessons, _reviews = get_prototype_detail(created_1.name, tlon_paths=p)
        assert lessons == "- 教训乙\n"

    def test_no_lessons_no_file(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        assert not (p.prototypes_dir() / created_1.name / "LESSONS.md").exists()
        _info, _behavior, lessons, _reviews = get_prototype_detail(created_1.name, tlon_paths=p)
        assert lessons is None

    def test_update_writes_lessons(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        update_prototype(created_1.name, lessons="- 新教训\n", tlon_paths=p)
        lessons_file = p.prototypes_dir() / created_1.name / "LESSONS.md"
        assert lessons_file.read_text(encoding="utf-8") == "- 新教训\n"

    def test_update_none_preserves_lessons(self, tmp_path):
        """None = keep (same semantics as parent/proposal_note)."""
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            lessons="- 保留我\n",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        update_prototype(created_1.name, description="只改描述", tlon_paths=p)
        lessons_file = p.prototypes_dir() / created_1.name / "LESSONS.md"
        assert lessons_file.read_text(encoding="utf-8") == "- 保留我\n"

    def test_approve_carries_lessons_to_trusted(self, tmp_path):
        """Approval is a whole-directory move — LESSONS.md rides along."""
        p = TlonPaths(home=tmp_path)
        created_1 = write_staging_prototype(
            VALID_BEHAVIOR,
            author_id="adam",
            lessons="- 随包\n",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        approve_prototype(created_1.name, tlon_paths=p)
        assert (p.prototypes_dir() / created_1.name / "LESSONS.md").exists()
        assert not (p.prototypes_staging_dir() / created_1.name).exists()


class TestInstantiateModel:
    """``instantiate`` requires the caller's ``model`` (ADR 0016): model
    selection is deployment policy, not genotype — it lands in the
    member's agent.yaml as ``llm.model_name``; a spec's top-level
    ``:llm`` key is rejected by the grammar."""

    BEHAVIOR_WITH_LLM = '{:version 2 :handlers {:mail-delivered work.fixtured59e2318/handle0} :llm {:model "deepseek/deepseek-chat"}}\n'

    def test_model_param_lands_in_agent_yaml(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        cfg = instantiate(created_1.name, "agent1", "新成员", model="openai/gpt-5", tlon_paths=p)
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
        with pytest.raises(PrototypeError, match="llm"):
            write_trusted_prototype(
                self.BEHAVIOR_WITH_LLM,
                tlon_paths=p,
                display_name="定址转发",
                modules=_FIXTURE_MODULES.get(self.BEHAVIOR_WITH_LLM),
            )

    def test_missing_model_raises(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        with pytest.raises(PrototypeError, match="model is required"):
            instantiate(created_1.name, "agent1", "新成员", model="", tlon_paths=p)


class TestInstantiateLessons:
    """``instantiate`` copies the prototype's LESSONS.md to the member's
    config dir (docs/design/prototypes.md)."""

    def test_copies_lessons_next_to_behavior(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            lessons="- 前辈教训\n",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        instantiate(created_1.name, "agent1", "新成员", model="openai/gpt-5", tlon_paths=p)
        lessons_file = p.agent_lessons_file("agent1")
        assert lessons_file.read_text(encoding="utf-8") == "- 前辈教训\n"

    def test_no_lessons_no_file(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        instantiate(created_1.name, "agent1", "新成员", model="openai/gpt-5", tlon_paths=p)
        assert not (tmp_path / "agent1" / "config" / "LESSONS.md").exists()


class TestInstantiateExecutionLevel:
    """``instantiate`` projects the spec's ``:execution`` declaration once
    into agent.yaml's ``docker.level`` (constitution 1,
    docs/design/execution-device.md); without the section the config
    default ("L1") applies unchanged."""

    L1_BEHAVIOR = "{:version 2 :handlers {:mail-delivered work.fixturec79a4a08/handle0} :execution {:level :L1}}\n"

    def test_execution_section_projects_docker_level(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            self.L1_BEHAVIOR,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(self.L1_BEHAVIOR),
        )
        cfg = instantiate(created_1.name, "agent1", "新成员", model="openai/gpt-5", tlon_paths=p)
        assert cfg.docker.level == "L1"
        on_disk = yaml_load_file(p.agent_config_file("agent1"))
        assert on_disk is not None
        assert on_disk.docker.level == "L1"

    def test_absent_section_keeps_default(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        cfg = instantiate(created_1.name, "agent1", "新成员", model="openai/gpt-5", tlon_paths=p)
        assert cfg.docker.level == "L1"


class TestDeletePrototype:
    def test_deletes_trusted_directory(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_BEHAVIOR,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
        )
        delete_prototype(created_1.name, tlon_paths=p)
        assert not (p.prototypes_dir() / created_1.name).exists()

    def test_missing_raises(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="no prototype named 'nope'"):
            delete_prototype("nope", tlon_paths=p)

    def test_path_traversal_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        for bad in ("..", "../x", "a/b", "a\\b"):
            with pytest.raises(PrototypeError):
                delete_prototype(bad, tlon_paths=p)


@pytest.mark.parametrize("writer", [write_trusted_prototype, write_staging_prototype])
def test_generated_ids_are_stable_opaque_and_unique(
    tmp_path: Path, writer: Callable[..., PrototypeInfo]
) -> None:
    from uuid import UUID

    p = TlonPaths(home=tmp_path)
    first = writer(
        VALID_BEHAVIOR,
        modules=_FIXTURE_MODULES[VALID_BEHAVIOR],
        display_name="定址转发",
        tlon_paths=p,
    )
    second = writer(
        VALID_BEHAVIOR,
        modules=_FIXTURE_MODULES[VALID_BEHAVIOR],
        display_name="定址转发",
        tlon_paths=p,
    )
    assert first.name != second.name
    assert first.name.startswith("p-")
    assert UUID(hex=first.name.removeprefix("p-")).version == 4
    detail, _, _, _ = get_prototype_detail(first.name, staging=not first.trusted, tlon_paths=p)
    assert detail.name == first.name
    assert detail.display_name == "定址转发"


@pytest.mark.parametrize("writer", [write_trusted_prototype, write_staging_prototype])
def test_writer_rejects_caller_supplied_id(
    tmp_path: Path, writer: Callable[..., PrototypeInfo]
) -> None:
    p = TlonPaths(home=tmp_path)
    with pytest.raises(TypeError, match="name"):
        writer(VALID_BEHAVIOR, name="developer", display_name="定址转发", tlon_paths=p)
    assert not p.prototypes_dir().exists()
    assert not p.prototypes_staging_dir().exists()


@pytest.mark.parametrize("label", ["", " ", "\n\t"])
@pytest.mark.parametrize("writer", [write_trusted_prototype, write_staging_prototype])
def test_empty_label_fails_before_id_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer: Callable[..., PrototypeInfo],
    label: str,
) -> None:
    from unittest.mock import Mock

    allocator = Mock(side_effect=AssertionError("must validate before assigning ID"))
    monkeypatch.setattr(f"{writer.__module__}.new_prototype_id", allocator)
    p = TlonPaths(home=tmp_path)
    with pytest.raises(PrototypeError, match="display_name"):
        writer(
            VALID_BEHAVIOR,
            modules=_FIXTURE_MODULES[VALID_BEHAVIOR],
            display_name=label,
            tlon_paths=p,
        )
    allocator.assert_not_called()
    assert not p.prototypes_dir().exists()
    assert not p.prototypes_staging_dir().exists()


def test_update_cannot_clear_behavior_label_or_write_other_fields(tmp_path: Path) -> None:
    p = TlonPaths(home=tmp_path)
    info = write_trusted_prototype(
        VALID_BEHAVIOR,
        display_name="定址转发",
        tlon_paths=p,
        modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR),
    )
    with pytest.raises(PrototypeError, match="display_name"):
        update_prototype(
            info.name,
            display_name="  ",
            behavior_edn=VALID_BEHAVIOR_V2,
            tlon_paths=p,
            modules=_FIXTURE_MODULES.get(VALID_BEHAVIOR_V2),
        )
    stored, behavior, _, _ = get_prototype_detail(info.name, tlon_paths=p)
    assert stored.display_name == "定址转发"
    assert behavior == VALID_BEHAVIOR
