"""Staging-area write gate (stage 4a trust gate).

``write_staging_prototype`` validates before anything touches disk: EDN
parse + ``MemberSpec.from_edn`` + the single ``validate_member_spec``
validator (host-less registry — Adam's host predicates must not pass).
Also covers the ``prototype-staging-list`` tool (): the staging
proposals an agent's digest can surface as pending human decisions.
"""

from pathlib import Path

import pytest

from tests.evolution_helpers import make_staging_tool, make_write_tool
from tlon.agents.prototypes.management import write_trusted_prototype
from tlon.agents.prototypes.staging import list_staging_prototypes, write_staging_prototype
from tlon.base.paths import TlonPaths
from tlon.evolution.ports import PrototypeError

_FIXTURE_MODULES = {
    "{:version 2 :handlers {:mail-delivered work.fixturec9a1e4ca/handle0}}\n": {
        "work/fixturec9a1e4ca.tlon": '(ns work.fixturec9a1e4ca (:export handle0))\n(defn handle0 [event] (let [msg (get event :mail) self (get event :self)] (let [action0 (mail-forward {:mail_id (mail-field msg :id) :to ["x@tlon.internal"]})] {:result nil})))\n'
    },
    "{:version 2 :handlers {:mail-delivered work.fixture8abb1c6f/handle0}}\n": {
        "work/fixture8abb1c6f.tlon": '(ns work.fixture8abb1c6f (:export handle0))\n(defn handle0 [event] (let [msg (get event :mail) self (get event :self)] (let [action0 (jump {:to ["x@tlon.internal"]})] {:result nil})))\n'
    },
    "{:version 2 :handlers {:mail-delivered work.fixturee9d33d1c/handle0} :execution {:level :L9}}\n": {
        "work/fixturee9d33d1c.tlon": '(ns work.fixturee9d33d1c (:export handle0))\n(defn handle0 [event] (let [msg (get event :mail) self (get event :self)] (let [action0 (mail-forward {:mail_id (mail-field msg :id) :to ["x@tlon.internal"]})] {:result nil})))\n'
    },
}


class TestWriteStagingValidation:
    def test_unknown_action_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="unknown function or special form: jump"):
            write_staging_prototype(
                "{:version 2 :handlers {:mail-delivered work.fixture8abb1c6f/handle0}}\n",
                tlon_paths=p,
                display_name="定址转发",
                modules=_FIXTURE_MODULES.get(
                    "{:version 2 :handlers {:mail-delivered work.fixture8abb1c6f/handle0}}\n"
                ),
            )
        assert not p.prototypes_staging_dir().exists()

    def test_invalid_execution_level_rejected(self, tmp_path):
        """The write gate inherits the spec grammar: an illegal
        ``:execution/:level`` fails validation before anything hits disk."""
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match=":execution/:level"):
            write_staging_prototype(
                "{:version 2 :handlers {:mail-delivered work.fixturee9d33d1c/handle0} :execution {:level :L9}}\n",
                tlon_paths=p,
                display_name="定址转发",
                modules=_FIXTURE_MODULES.get(
                    "{:version 2 :handlers {:mail-delivered work.fixturee9d33d1c/handle0} :execution {:level :L9}}\n"
                ),
            )
        assert not p.prototypes_staging_dir().exists()


BUNDLE_BEHAVIOR = "{:version 2 :handlers {:mail-delivered work.reply/handle} :fuel 1000}"
BUNDLE_MODULES = {
    "work/reply.tlon": "(ns work.reply (:require [work.helper :as h]) (:export handle)) (defn handle [event] (h/result))",
    "work/helper.tlon": '(ns work.helper (:export result)) (defn result [] {:result "ok"})',
}

VALID_SPEC = "{:version 2 :handlers {:mail-delivered work.fixturec9a1e4ca/handle0}}\n"


class TestListStagingPrototypesTool:
    """prototype-staging-list (): untrusted proposals awaiting the
    human trust decision, listed with author for audit."""

    async def test_lists_staged_proposals(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_staging_prototype(
            VALID_SPEC,
            display_name="夜猫子",
            description="夜间值守",
            author_id="lin-zhiyao",
            tlon_paths=p,
            modules=_FIXTURE_MODULES.get(VALID_SPEC),
        )
        tool = make_staging_tool(lister=lambda: list_staging_prototypes(p))
        result = await tool.execute_context({})
        assert result.success
        assert result.data == [
            {
                "name": created_1.name,
                "display_name": "夜猫子",
                "description": "夜间值守",
                "author": "lin-zhiyao",
                "parent": None,
                "generation": 1,
            }
        ]

    async def test_empty_staging(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        tool = make_staging_tool(lister=lambda: list_staging_prototypes(p))
        result = await tool.execute_context({})
        assert result.success
        assert result.data == []

    def test_tool_schema(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        tool = make_staging_tool(lister=lambda: list_staging_prototypes(p))
        assert tool.name == "prototype-staging-list"
        assert tool.parameters["required"] == []


class TestStagingLineageFields:
    """write_staging_prototype lineage fields (docs/design/prototypes.md):
    :parent links a
    proposal to the prototype it derives from; :generation is derived
    server-side as the parent's + 1."""

    def test_root_proposal_defaults(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        info = write_staging_prototype(
            VALID_SPEC,
            author_id="adam",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_SPEC),
        )
        assert info.parent is None
        assert info.generation == 1
        assert info.proposal_note is None

    def test_child_derives_generation_from_trusted_parent(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_trusted_prototype(
            VALID_SPEC,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_SPEC),
        )
        info = write_staging_prototype(
            VALID_SPEC,
            author_id="adam",
            parent=created_1.name,
            proposal_note="换上更严格的门控",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_SPEC),
        )
        assert info.parent == created_1.name
        assert info.generation == 2
        assert info.proposal_note == "换上更严格的门控"

    def test_parent_may_live_in_staging(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        created_1 = write_staging_prototype(
            VALID_SPEC,
            author_id="adam",
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_SPEC),
        )
        info = write_staging_prototype(
            VALID_SPEC,
            author_id="adam",
            parent=created_1.name,
            tlon_paths=p,
            display_name="定址转发",
            modules=_FIXTURE_MODULES.get(VALID_SPEC),
        )
        assert info.generation == 2

    def test_unknown_parent_rejected_nothing_on_disk(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="parent prototype 'ghost' not found"):
            write_staging_prototype(
                VALID_SPEC,
                author_id="adam",
                parent="ghost",
                tlon_paths=p,
                display_name="定址转发",
                modules=_FIXTURE_MODULES.get(VALID_SPEC),
            )
        assert not p.prototypes_staging_dir().exists()

    def test_invalid_parent_name_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        with pytest.raises(PrototypeError, match="invalid prototype name"):
            write_staging_prototype(
                VALID_SPEC,
                author_id="adam",
                parent="../x",
                tlon_paths=p,
                display_name="定址转发",
                modules=_FIXTURE_MODULES.get(VALID_SPEC),
            )


class TestWritePrototypeToolLessons:
    """prototype-write passes the optional ``lessons`` parameter through to
    the staging writer (docs/design/prototypes.md): the prototype lands
    with LESSONS.md."""

    async def test_lessons_passthrough(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        tool = make_write_tool(
            author_id="adam",
            writer=lambda *a, **kw: write_staging_prototype(
                *a,
                tlon_paths=p,
                **kw,
            ),
        )
        result = await tool.execute_context(
            {
                "behavior": BUNDLE_BEHAVIOR,
                "modules": BUNDLE_MODULES,
                "lessons": "- 教训\n",
                "display_name": "定址转发",
            }
        )
        assert result.success
        lessons_file = p.prototypes_staging_dir() / result.data["name"] / "LESSONS.md"
        assert lessons_file.read_text(encoding="utf-8") == "- 教训\n"

    async def test_no_lessons_no_file(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        tool = make_write_tool(
            author_id="adam",
            writer=lambda *a, **kw: write_staging_prototype(
                *a,
                tlon_paths=p,
                **kw,
            ),
        )
        result = await tool.execute_context(
            {"behavior": BUNDLE_BEHAVIOR, "modules": BUNDLE_MODULES, "display_name": "定址转发"}
        )
        assert result.success
        assert not (p.prototypes_staging_dir() / result.data["name"] / "LESSONS.md").exists()


class TestWritePrototypeSolvency:
    """Proposal solvency gate (docs/design/prototypes.md, agent side): an
    insolvent bloodline
    loses proposal eligibility; the check fails open."""

    async def test_insolvent_bloodline_rejected_nothing_on_disk(self, tmp_path):
        p = TlonPaths(home=tmp_path)

        async def _insolvent(prototype: str) -> bool:
            return False

        tool = make_write_tool(
            author_id="adam",
            writer=lambda *a, **kw: write_staging_prototype(
                *a,
                tlon_paths=p,
                **kw,
            ),
            prototype="broke",
            solvency=_insolvent,
        )
        result = await tool.execute_context(
            {"behavior": BUNDLE_BEHAVIOR, "modules": BUNDLE_MODULES, "display_name": "定址转发"}
        )
        assert not result.success
        assert "insolvent" in result.error
        assert not p.prototypes_staging_dir().exists()

    async def test_solvency_failure_is_fail_open(self, tmp_path):
        p = TlonPaths(home=tmp_path)

        async def _boom(prototype: str) -> bool:
            raise OSError("fitness derivation broke")

        tool = make_write_tool(
            author_id="adam",
            writer=lambda *a, **kw: write_staging_prototype(
                *a,
                tlon_paths=p,
                **kw,
            ),
            prototype="broke",
            solvency=_boom,
        )
        result = await tool.execute_context(
            {"behavior": BUNDLE_BEHAVIOR, "modules": BUNDLE_MODULES, "display_name": "定址转发"}
        )
        assert result.success, result.error

    async def test_manual_member_never_gated(self, tmp_path):
        """An author without a prototype (manual member) has no bloodline
        to judge — the gate does not apply."""
        p = TlonPaths(home=tmp_path)

        async def _insolvent(prototype: str) -> bool:
            return False

        tool = make_write_tool(
            author_id="adam",
            writer=lambda *a, **kw: write_staging_prototype(
                *a,
                tlon_paths=p,
                **kw,
            ),
            prototype=None,
            solvency=_insolvent,
        )
        result = await tool.execute_context(
            {"behavior": BUNDLE_BEHAVIOR, "modules": BUNDLE_MODULES, "display_name": "定址转发"}
        )
        assert result.success, result.error


async def test_write_tool_rejects_old_name_and_requires_behavior_label(tmp_path: Path) -> None:
    from unittest.mock import Mock

    writer = Mock()
    tool = make_write_tool(author_id="adam", writer=writer)
    assert "name" not in tool.parameters["properties"]
    assert "display_name" in tool.parameters["required"]
    for args in (
        {
            "name": "developer",
            "display_name": "定址转发",
            "behavior": BUNDLE_BEHAVIOR,
            "modules": BUNDLE_MODULES,
        },
        {"behavior": BUNDLE_BEHAVIOR, "modules": BUNDLE_MODULES},
        {"display_name": "  ", "behavior": BUNDLE_BEHAVIOR, "modules": BUNDLE_MODULES},
    ):
        result = await tool.execute_context(args)
        assert not result.success
        assert "name" in result.error
    writer.assert_not_called()


async def test_machine_writer_requires_v2_complete_module_bundle(tmp_path: Path) -> None:
    from unittest.mock import Mock

    writer = Mock()
    tool = make_write_tool(author_id="adam", writer=writer)
    for package in (
        {"behavior": "{:version 1 :rules []}", "modules": BUNDLE_MODULES},
        {"behavior": BUNDLE_BEHAVIOR},
        {"behavior": BUNDLE_BEHAVIOR, "modules": {}},
        {"behavior": BUNDLE_BEHAVIOR, "modules": {"work/reply.tlon": 17}},
    ):
        result = await tool.execute_context({**package, "display_name": "完整程序"})
        assert not result.success
    writer.assert_not_called()
