"""Tests for the behavior prototype library (tlon.agents.prototypes)."""

from importlib import resources

import pytest

from tlon.agents import prototypes as prototypes_mod
from tlon.agents.edn import Keyword, dumps, loads
from tlon.agents.prototypes import (
    PrototypeError,
    PrototypeInfo,
    get_prototype_info,
    instantiate,
    list_prototypes,
    seed_prototypes,
)
from tlon.agents.spec import AgentSpec
from tlon.base.config import _yaml_load_file
from tlon.base.events.event import EventType
from tlon.base.paths import TlonPaths
from tlon.tools.prototypes.list_prototypes import ListPrototypesTool

BUILTIN_NAMES = {"deep-thinker", "fast-responder", "archivist", "relay"}
REACT_BUILTINS = {"deep-thinker", "fast-responder", "archivist"}
SCRIPT_BUILTINS = {"relay"}
PROTOTYPE_FILES = {"prototype.edn", "behavior.tlon", "IDENTITY.md", "SOUL.md", "RULES.md"}
SCRIPT_PROTOTYPE_FILES = {"prototype.edn", "script.tlon"}


def _use_tmp_paths(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TlonPaths:
    """Isolate ~/.tlon: point prototypes + spec modules at a tmp home."""
    test_paths = TlonPaths(home=tmp_path)
    monkeypatch.setattr("tlon.agents.prototypes.paths", test_paths)
    monkeypatch.setattr("tlon.agents.spec.paths", test_paths)
    monkeypatch.setattr("tlon.agents.script_spec.paths", test_paths)
    return test_paths


class TestPackageResources:
    def test_builtin_prototypes_shipped_in_package(self):
        package_root = resources.files(prototypes_mod.PROTOTYPE_PACKAGE)
        dirs = {
            entry.name
            for entry in package_root.iterdir()
            if entry.is_dir() and not entry.name.startswith(("_", "."))
        }
        assert dirs == BUILTIN_NAMES
        for name in REACT_BUILTINS:
            files = {entry.name for entry in (package_root / name).iterdir()}
            assert files == PROTOTYPE_FILES
        for name in SCRIPT_BUILTINS:
            files = {entry.name for entry in (package_root / name).iterdir()}
            assert files == SCRIPT_PROTOTYPE_FILES

    def test_builtin_behavior_files_parse_and_validate(self):
        package_root = resources.files(prototypes_mod.PROTOTYPE_PACKAGE)
        for name in REACT_BUILTINS:
            raw = (package_root / name / "behavior.tlon").read_text(encoding="utf-8")
            spec = AgentSpec.from_edn(loads(raw))
            assert spec.version == 1

    def test_builtin_script_files_parse_and_validate(self):
        """P5b: the relay prototype's script.tlon is a total ScriptSpec."""
        from tlon.agents.script_spec import ScriptSpec

        package_root = resources.files(prototypes_mod.PROTOTYPE_PACKAGE)
        for name in SCRIPT_BUILTINS:
            raw = (package_root / name / "script.tlon").read_text(encoding="utf-8")
            spec = ScriptSpec.from_edn(loads(raw))
            assert spec.version == 1

    def test_builtin_behavior_specs_totality(self):
        """Total spec: every builtin behavior.tlon must satisfy the full schema
        (no defaults filled in), and carry the designed loop parameters."""
        package_root = resources.files(prototypes_mod.PROTOTYPE_PACKAGE)
        specs = {
            name: AgentSpec.from_edn(
                loads((package_root / name / "behavior.tlon").read_text(encoding="utf-8"))
            )
            for name in REACT_BUILTINS
        }
        assert [dumps(form) for form in specs["deep-thinker"].loop.stages] == [
            "(think)",
            "(act)",
            "(critique)",
            "(revise {:if (critique-failed?)})",
        ]
        assert specs["deep-thinker"].loop.max_iterations == 25
        assert specs["fast-responder"].loop.max_iterations == 3
        assert specs["archivist"].loop.max_iterations == 10

    def test_builtin_metadata_files_parse(self):
        package_root = resources.files(prototypes_mod.PROTOTYPE_PACKAGE)
        for name in BUILTIN_NAMES:
            raw = (package_root / name / "prototype.edn").read_text(encoding="utf-8")
            data = loads(raw)
            assert str(data[Keyword("name")]) == name
            assert data[Keyword("display-name")]
            assert data[Keyword("description")]

    def test_relay_metadata_declares_script_runtime(self):
        """P5b: relay is the first script prototype — trusted, :runtime :script."""
        package_root = resources.files(prototypes_mod.PROTOTYPE_PACKAGE)
        raw = (package_root / "relay" / "prototype.edn").read_text(encoding="utf-8")
        data = loads(raw)
        assert data[Keyword("trusted")] is True
        assert str(data[Keyword("runtime")]) == "script"


class TestSeedPrototypes:
    def test_seed_copies_builtin_prototypes(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        copied = seed_prototypes()
        expected = len(REACT_BUILTINS) * len(PROTOTYPE_FILES) + len(SCRIPT_BUILTINS) * len(
            SCRIPT_PROTOTYPE_FILES
        )
        assert copied == expected
        for name in REACT_BUILTINS:
            proto_dir = test_paths.prototypes_dir() / name
            assert {f.name for f in proto_dir.iterdir()} == PROTOTYPE_FILES
        for name in SCRIPT_BUILTINS:
            proto_dir = test_paths.prototypes_dir() / name
            assert {f.name for f in proto_dir.iterdir()} == SCRIPT_PROTOTYPE_FILES

    def test_seed_is_idempotent_and_preserves_user_edits(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        edited = test_paths.prototypes_dir() / "deep-thinker" / "RULES.md"
        edited.write_text("用户自定义守则\n", encoding="utf-8")

        copied = seed_prototypes()

        assert copied == 0
        assert edited.read_text(encoding="utf-8") == "用户自定义守则\n"


class TestListPrototypes:
    def test_lists_seeded_prototypes_with_metadata(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        infos = {info.name: info for info in list_prototypes()}
        assert set(infos) == BUILTIN_NAMES
        assert infos["deep-thinker"].display_name == "深度思考者"
        assert infos["deep-thinker"].description

    def test_runtime_metadata_per_prototype(self, tmp_path, monkeypatch):
        """P5b: :runtime defaults to react; relay declares script."""
        _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        infos = {info.name: info for info in list_prototypes()}
        assert infos["relay"].runtime == "script"
        assert infos["relay"].trusted is True
        for name in REACT_BUILTINS:
            assert infos[name].runtime == "react"

    def test_script_only_directory_qualifies(self, tmp_path, monkeypatch):
        """P5b: a directory qualifies with script.tlon alone (no behavior)."""
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        proto_dir = test_paths.prototypes_dir() / "my-relay"
        proto_dir.mkdir(parents=True)
        (proto_dir / "script.tlon").write_text(
            '{:version 1 :rules [{:name "r" :on :mail-delivered '
            ':actions [(forward {:to ["a@tlon.internal"]})]}]}\n',
            encoding="utf-8",
        )

        infos = {info.name: info for info in list_prototypes()}
        assert "my-relay" in infos
        assert get_prototype_info("my-relay") is not None

    def test_user_added_prototype_is_visible(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        custom = test_paths.prototypes_dir() / "my-custom"
        custom.mkdir(parents=True)
        (custom / "behavior.tlon").write_text("{:version 1}\n", encoding="utf-8")

        infos = {info.name: info for info in list_prototypes()}

        assert "my-custom" in infos
        # No prototype.edn -> fall back to directory name
        assert infos["my-custom"] == PrototypeInfo(
            name="my-custom", display_name="my-custom", description=""
        )

    def test_missing_directory_returns_empty(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        assert list_prototypes() == []

    def test_user_metadata_edit_is_visible(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        metadata = test_paths.prototypes_dir() / "archivist" / "prototype.edn"
        metadata.write_text(
            '{:name :archivist :display-name "归档员" :description "改过的描述"}\n',
            encoding="utf-8",
        )
        infos = {info.name: info for info in list_prototypes()}
        assert infos["archivist"].display_name == "归档员"
        assert infos["archivist"].description == "改过的描述"


class TestInstantiate:
    def test_copies_persona_and_records_prototype(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()

        agent = instantiate("deep-thinker", agent_id="dt-001", agent_name="深思")

        config_dir = test_paths.prototypes_dir().parent / "dt-001" / "config"
        for filename in ("IDENTITY.md", "SOUL.md", "RULES.md"):
            copied = (config_dir / filename).read_text(encoding="utf-8")
            original = (test_paths.prototypes_dir() / "deep-thinker" / filename).read_text(
                encoding="utf-8"
            )
            assert copied == original
        assert (config_dir / "behavior.tlon").exists()

        # agent.yaml written through the standard save path, prototype recorded
        reloaded = _yaml_load_file(test_paths.agent_config_file("dt-001"))
        assert reloaded is not None
        assert reloaded.prototype == "deep-thinker"
        assert agent.prototype == "deep-thinker"
        assert reloaded.name == "深思"

    def test_behavior_file_copied_verbatim_and_yaml_written(self, tmp_path, monkeypatch):
        """Instantiate without overrides: behavior.tlon is copied byte-for-byte
        from the prototype, and agent.yaml is written through the save path."""
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()

        instantiate("archivist", agent_id="ar-001", agent_name="归档")

        config_dir = test_paths.agent_config_dir("ar-001")
        copied = (config_dir / "behavior.tlon").read_text(encoding="utf-8")
        original = (test_paths.prototypes_dir() / "archivist" / "behavior.tlon").read_text(
            encoding="utf-8"
        )
        assert copied == original

        reloaded = _yaml_load_file(test_paths.agent_config_file("ar-001"))
        assert reloaded is not None
        assert reloaded.id == "ar-001"
        assert reloaded.name == "归档"
        assert reloaded.prototype == "archivist"

    def test_missing_prototype_raises(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        with pytest.raises(PrototypeError, match="not found"):
            instantiate("no-such-proto", agent_id="x-001", agent_name="X")

    def test_agent_id_conflict_raises(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        instantiate("deep-thinker", agent_id="dt-003", agent_name="深思")
        with pytest.raises(PrototypeError, match="already exists"):
            instantiate("archivist", agent_id="dt-003", agent_name="归档")

    def test_proactive_flag_recorded(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        instantiate("fast-responder", agent_id="fr-001", agent_name="快手", proactive=True)
        reloaded = _yaml_load_file(test_paths.agent_config_file("fr-001"))
        assert reloaded is not None
        assert reloaded.proactive is True

    def test_script_prototype_instantiation(self, tmp_path, monkeypatch):
        """P5b: instantiating a script prototype copies script.tlon verbatim
        and writes agent.yaml with runtime="script" (no behavior file)."""
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()

        agent = instantiate("relay", agent_id="rl-001", agent_name="转发")

        config_dir = test_paths.agent_config_dir("rl-001")
        copied = (config_dir / "script.tlon").read_text(encoding="utf-8")
        original = (test_paths.prototypes_dir() / "relay" / "script.tlon").read_text(
            encoding="utf-8"
        )
        assert copied == original
        assert not (config_dir / "behavior.tlon").exists()

        reloaded = _yaml_load_file(test_paths.agent_config_file("rl-001"))
        assert reloaded is not None
        assert reloaded.runtime == "script"
        assert reloaded.prototype == "relay"
        assert agent.runtime == "script"

    def test_script_runtime_without_script_file_raises(self, tmp_path, monkeypatch):
        """Metadata declaring :runtime :script without a script.tlon is an error."""
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        proto_dir = test_paths.prototypes_dir() / "broken-script"
        proto_dir.mkdir(parents=True)
        (proto_dir / "behavior.tlon").write_text("{:version 1}\n", encoding="utf-8")
        (proto_dir / "prototype.edn").write_text(
            "{:name :broken-script :runtime :script}\n", encoding="utf-8"
        )

        with pytest.raises(PrototypeError, match="no script.tlon"):
            instantiate("broken-script", agent_id="bs-001", agent_name="坏")


class TestEndToEndSpecInheritance:
    """Stage 5 completion criterion: instantiate from a prototype, and the
    total behavior spec must be visible through AgentSpec.load."""

    def test_deep_thinker_explicit_stage_program(self, tmp_path, monkeypatch):
        """Phase 7c: deep-thinker runs an explicit stage program
        (think/act/critique + conditional revise), not the default react pair."""
        _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        instantiate("deep-thinker", agent_id="dt-012", agent_name="深思")

        spec = AgentSpec.load("dt-012")

        assert [dumps(form) for form in spec.loop.stages] == [
            "(think)",
            "(act)",
            "(critique)",
            "(revise {:if (critique-failed?)})",
        ]
        assert dumps(spec.loop.until) == "(or (iteration-limit-reached?) (error-limit-reached?))"
        assert spec.loop.max_iterations == 25

    def test_fast_responder_instance_spec(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        instantiate("fast-responder", agent_id="fr-010", agent_name="快手")

        spec = AgentSpec.load("fr-010")

        assert spec.loop.max_iterations == 3
        # Explicit section vector: no (knowledge), short mail history.
        assert spec.context.sections == (
            loads("(persona)"),
            loads("(protocol)"),
            loads("(mail-history 5)"),
        )

    def test_archivist_instance_spec(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        instantiate("archivist", agent_id="ar-010", agent_name="归档")

        spec = AgentSpec.load("ar-010")

        assert spec.on_complete.save_interaction is True
        assert spec.on_complete.reflect.interval_minutes == 5

    def test_relay_instance_script_spec(self, tmp_path, monkeypatch):
        """P5b: a relay instance's script.tlon loads through ScriptSpec.load."""
        from tlon.agents.script_spec import ScriptSpec

        _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        instantiate("relay", agent_id="rl-010", agent_name="转发")

        spec = ScriptSpec.load("rl-010")

        assert spec.version == 1
        assert [rule.name for rule in spec.rules] == ["relay"]
        assert spec.rules[0].on is EventType.MAIL_DELIVERED


class TestListPrototypesTool:
    @pytest.mark.asyncio
    async def test_tool_returns_prototype_list(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()

        result = await ListPrototypesTool().execute()

        assert result.success is True
        by_name = {entry["name"]: entry for entry in result.data}
        assert set(by_name) == BUILTIN_NAMES
        assert by_name["archivist"]["display_name"] == "档案管理者"
        assert by_name["archivist"]["description"]

    @pytest.mark.asyncio
    async def test_tool_empty_library(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)

        result = await ListPrototypesTool().execute()

        assert result.success is True
        assert result.data == []
