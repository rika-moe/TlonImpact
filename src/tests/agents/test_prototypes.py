"""Tests for the behavior prototype library (tlon.agents.prototypes)."""

from importlib import resources
from types import SimpleNamespace

import pytest

from tlon.agents import prototypes as prototypes_mod
from tlon.agents.edn import Keyword, loads
from tlon.agents.prototypes import (
    PrototypeError,
    PrototypeInfo,
    instantiate,
    list_prototypes,
    seed_prototypes,
)
from tlon.agents.spec import AgentSpec
from tlon.base.config import AgentConfig, _yaml_load_file
from tlon.base.paths import TlonPaths
from tlon.tools.prototypes.list_prototypes import ListPrototypesTool

BUILTIN_NAMES = {"deep-thinker", "fast-responder", "archivist"}
PROTOTYPE_FILES = {"prototype.edn", "behavior.tlon", "IDENTITY.md", "SOUL.md", "RULES.md"}


def _use_tmp_paths(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TlonPaths:
    """Isolate ~/.tlon: point prototypes + spec modules at a tmp home."""
    test_paths = TlonPaths(home=tmp_path)
    monkeypatch.setattr("tlon.agents.prototypes.paths", test_paths)
    monkeypatch.setattr("tlon.agents.spec.paths", test_paths)
    return test_paths


def _fake_agent(agent_id: str) -> SimpleNamespace:
    config = AgentConfig(id=agent_id, name="TestAgent")
    memory_config = SimpleNamespace(reflection_enabled=True, reflection_interval_minutes=15)
    return SimpleNamespace(config=config, memory=SimpleNamespace(config=memory_config))


class TestPackageResources:
    def test_builtin_prototypes_shipped_in_package(self):
        package_root = resources.files(prototypes_mod.PROTOTYPE_PACKAGE)
        dirs = {
            entry.name
            for entry in package_root.iterdir()
            if entry.is_dir() and not entry.name.startswith(("_", "."))
        }
        assert dirs == BUILTIN_NAMES
        for name in BUILTIN_NAMES:
            files = {entry.name for entry in (package_root / name).iterdir()}
            assert files == PROTOTYPE_FILES

    def test_builtin_behavior_files_parse_and_validate(self):
        package_root = resources.files(prototypes_mod.PROTOTYPE_PACKAGE)
        for name in BUILTIN_NAMES:
            raw = (package_root / name / "behavior.tlon").read_text(encoding="utf-8")
            spec = AgentSpec.from_edn(loads(raw))
            assert spec.version == 1

    def test_builtin_metadata_files_parse(self):
        package_root = resources.files(prototypes_mod.PROTOTYPE_PACKAGE)
        for name in BUILTIN_NAMES:
            raw = (package_root / name / "prototype.edn").read_text(encoding="utf-8")
            data = loads(raw)
            assert str(data[Keyword("name")]) == name
            assert data[Keyword("display-name")]
            assert data[Keyword("description")]


class TestSeedPrototypes:
    def test_seed_copies_builtin_prototypes(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        copied = seed_prototypes()
        assert copied == len(BUILTIN_NAMES) * len(PROTOTYPE_FILES)
        for name in BUILTIN_NAMES:
            proto_dir = test_paths.prototypes_dir() / name
            assert {f.name for f in proto_dir.iterdir()} == PROTOTYPE_FILES

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

    def test_overrides_deep_merge_preserves_other_fields(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()

        instantiate(
            "deep-thinker",
            agent_id="dt-002",
            agent_name="深思",
            overrides={Keyword("loop"): {Keyword("max-iterations"): 5}},
        )

        behavior_file = test_paths.agent_config_dir("dt-002") / "behavior.tlon"
        data = loads(behavior_file.read_text(encoding="utf-8"))
        assert data[Keyword("loop")][Keyword("max-iterations")] == 5
        # Fields not overridden stay from the prototype
        assert data[Keyword("on-complete")][Keyword("reflect")][Keyword("interval-minutes")] == 10
        assert data[Keyword("version")] == 1

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


class TestEndToEndSpecInheritance:
    """Stage 5 completion criterion: instantiate from deep-thinker, behavior
    inheritance + override must be visible through AgentSpec.load."""

    def test_deep_thinker_instance_spec(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        instantiate("deep-thinker", agent_id="dt-010", agent_name="深思")

        spec = AgentSpec.load(_fake_agent("dt-010"))
        base = AgentSpec.default(_fake_agent("dt-010"))

        # Prototype overrides applied
        assert spec.loop.max_iterations == 25
        assert spec.on_complete.reflect.interval_minutes == 10
        # Everything else inherited from DefaultSpec
        assert spec.context == base.context
        assert spec.trigger == base.trigger
        assert spec.on_complete.save_interaction == base.on_complete.save_interaction
        assert spec.loop.max_consecutive_errors == base.loop.max_consecutive_errors

    def test_fast_responder_instance_spec(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        instantiate("fast-responder", agent_id="fr-010", agent_name="快手")

        spec = AgentSpec.load(_fake_agent("fr-010"))

        assert spec.loop.max_iterations == 3
        assert spec.context.knowledge is False
        assert spec.context.mail_history == 5

    def test_archivist_instance_spec(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        instantiate("archivist", agent_id="ar-010", agent_name="归档")

        spec = AgentSpec.load(_fake_agent("ar-010"))

        assert spec.on_complete.save_interaction is True
        assert spec.on_complete.reflect.interval_minutes == 5

    def test_instantiated_override_flows_into_spec(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        instantiate(
            "deep-thinker",
            agent_id="dt-011",
            agent_name="深思",
            overrides={Keyword("loop"): {Keyword("max-iterations"): 7}},
        )

        spec = AgentSpec.load(_fake_agent("dt-011"))

        assert spec.loop.max_iterations == 7
        assert spec.on_complete.reflect.interval_minutes == 10


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
