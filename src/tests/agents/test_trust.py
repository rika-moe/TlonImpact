"""Tests for the stage-4a trust gate (docs/design/dsl-agents.md).

Covers:
- trust metadata: builtin/user prototypes trusted, staging untrusted,
  missing ``:trusted`` defaults to trusted
- write_staging_prototype: validate-before-write (EDN syntax / total
  schema), name conflicts, path-injection charset
- approve/reject: promotion flips ``:trusted true`` (author kept),
  rejection deletes, guards on both
- spawn trust gate: untrusted prototype refused with reason
  "untrusted_prototype" before any budget check; spawnAgent error readable
- writePrototype tool: schema, pending_approval result, readable errors
- spec provenance: source_hash = sha256-12 of the behavior file, stamped on
  the agent.session span; a missing/invalid behavior file refuses boot
"""

import contextlib
import hashlib
import tempfile
from importlib import resources
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.agents import prototypes as prototypes_mod
from tlon.agents.agent_registry import AgentRegistry
from tlon.agents.edn import Keyword, loads
from tlon.agents.prototypes import (
    BEHAVIOR_FILE,
    METADATA_FILE,
    PrototypeError,
    approve_prototype,
    get_prototype_info,
    list_prototypes,
    list_staging_prototypes,
    reject_prototype,
    seed_prototypes,
    write_staging_prototype,
)
from tlon.agents.spawn_errors import SpawnRejectedError
from tlon.agents.spec import AgentSpec, SpecValidationError, standard_behavior_edn
from tlon.agents.stages.services import StageServices
from tlon.base.config import AgentConfig, ConfigService
from tlon.base.paths import TlonPaths
from tlon.tools.agents.spawn_agent import SpawnAgentTool
from tlon.tools.agents.write_prototype import WritePrototypeTool

VALID_BEHAVIOR = standard_behavior_edn()

RESIDENT = "resident-1"


def _use_tmp_paths(monkeypatch: pytest.MonkeyPatch, tmp_path) -> TlonPaths:
    """Isolate ~/.tlon: point prototypes + spec modules at a tmp home."""
    test_paths = TlonPaths(home=tmp_path)
    monkeypatch.setattr("tlon.agents.prototypes.paths", test_paths)
    monkeypatch.setattr("tlon.agents.spec.paths", test_paths)
    return test_paths


def _make_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock()
    bus.subscribe = MagicMock(return_value="sub-1")
    bus.unsubscribe = MagicMock()
    return bus


async def _make_registry(
    tlon_paths: TlonPaths, configs: dict[str, AgentConfig]
) -> tuple[AgentRegistry, MagicMock]:
    bus = _make_bus()
    provider = ConfigService(event_bus=bus, tlon_home=tlon_paths.tlon_home())
    await provider.initialize()
    for config in configs.values():
        provider.save_agent(config)
    registry = AgentRegistry(
        event_bus=bus,
        agent_info_provider=provider,
        paths=tlon_paths,
    )
    for agent_id in configs:
        registry.register(agent_id)
    return registry, bus


class TestTrustMetadata:
    def test_builtin_prototypes_declare_trusted(self):
        package_root = resources.files(prototypes_mod.PROTOTYPE_PACKAGE)
        for name in ("deep-thinker", "fast-responder", "archivist"):
            data = loads((package_root / name / METADATA_FILE).read_text(encoding="utf-8"))
            assert data[Keyword("trusted")] is True

    def test_seeded_prototypes_are_trusted(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        infos = {info.name: info for info in list_prototypes()}
        assert infos, "expected seeded prototypes"
        assert all(info.trusted for info in infos.values())

    def test_user_prototype_without_trusted_field_defaults_trusted(
        self, tmp_path, monkeypatch
    ):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        custom = test_paths.prototypes_dir() / "handwritten"
        custom.mkdir(parents=True)
        (custom / BEHAVIOR_FILE).write_text("{:version 1}\n", encoding="utf-8")
        (custom / METADATA_FILE).write_text(
            '{:name :handwritten :display-name "手写" :description "无 trusted 字段"}\n',
            encoding="utf-8",
        )
        info = get_prototype_info("handwritten")
        assert info is not None
        assert info.trusted is True


class TestWriteStaging:
    def test_valid_write_lands_untrusted_with_author(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        info = write_staging_prototype(
            "idea-bot",
            VALID_BEHAVIOR,
            persona={"IDENTITY.md": "我是一个点子机器", "SOUL.md": "", "RULES.md": "规则"},
            display_name="点子机器",
            description="脑暴专用",
            author_id="agent-42",
        )
        assert info.trusted is False
        assert info.author == "agent-42"
        assert info.display_name == "点子机器"

        staging_dir = test_paths.prototypes_staging_dir() / "idea-bot"
        assert (staging_dir / BEHAVIOR_FILE).read_text(encoding="utf-8") == VALID_BEHAVIOR
        assert (staging_dir / "IDENTITY.md").exists()
        # Empty persona content is skipped
        assert not (staging_dir / "SOUL.md").exists()
        assert (staging_dir / "RULES.md").exists()

        metadata = loads((staging_dir / METADATA_FILE).read_text(encoding="utf-8"))
        assert metadata[Keyword("trusted")] is False
        assert metadata[Keyword("author")] == "agent-42"

        staged = list_staging_prototypes()
        assert [p.name for p in staged] == ["idea-bot"]
        # Not visible in the trusted library
        assert list_prototypes() == []

    def test_edn_syntax_error_rejected_without_disk_write(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        with pytest.raises(PrototypeError, match="parse failed"):
            write_staging_prototype("broken", "{:version 1", author_id="a")
        assert not (test_paths.prototypes_staging_dir() / "broken").exists()

    def test_missing_version_rejected(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        with pytest.raises(PrototypeError, match=":version"):
            write_staging_prototype("noversion", "{:loop {:max-iterations 3}}", author_id="a")
        assert not (test_paths.prototypes_staging_dir() / "noversion").exists()

    def test_bad_field_type_rejected(self, tmp_path, monkeypatch):
        bad = VALID_BEHAVIOR.replace(":max-iterations 10", ':max-iterations "five"')
        with pytest.raises(PrototypeError, match="max-iterations"):
            write_staging_prototype("badtype", bad, author_id="a")

    def test_name_conflict_with_trusted_library(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        seed_prototypes()
        with pytest.raises(PrototypeError, match="already exists"):
            write_staging_prototype("deep-thinker", VALID_BEHAVIOR, author_id="a")

    def test_name_conflict_within_staging(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        write_staging_prototype("dup", VALID_BEHAVIOR, author_id="a")
        with pytest.raises(PrototypeError, match="already exists"):
            write_staging_prototype("dup", VALID_BEHAVIOR, author_id="a")

    @pytest.mark.parametrize("bad_name", ["../evil", "a/b", "a b", "", ".", "a.b"])
    def test_path_injection_names_rejected(self, tmp_path, monkeypatch, bad_name):
        _use_tmp_paths(monkeypatch, tmp_path)
        with pytest.raises(PrototypeError, match="invalid prototype name"):
            write_staging_prototype(bad_name, VALID_BEHAVIOR, author_id="a")

    def test_unknown_persona_file_rejected(self, tmp_path, monkeypatch):
        with pytest.raises(PrototypeError, match="persona"):
            write_staging_prototype(
                "badpersona",
                VALID_BEHAVIOR,
                persona={"EVIL.md": "x"},
                author_id="a",
            )


class TestApproveReject:
    def _stage(self, monkeypatch, tmp_path, name="idea-bot") -> TlonPaths:
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        write_staging_prototype(
            name, VALID_BEHAVIOR, display_name="点子机器", author_id="agent-42"
        )
        return test_paths

    def test_approve_promotes_to_trusted_library(self, tmp_path, monkeypatch):
        test_paths = self._stage(monkeypatch, tmp_path)
        info = approve_prototype("idea-bot")

        assert info.trusted is True
        assert info.author == "agent-42"
        assert not (test_paths.prototypes_staging_dir() / "idea-bot").exists()
        assert (test_paths.prototypes_dir() / "idea-bot" / BEHAVIOR_FILE).exists()

        by_name = {p.name: p for p in list_prototypes()}
        assert by_name["idea-bot"].trusted is True
        assert list_staging_prototypes() == []

        metadata = loads(
            (test_paths.prototypes_dir() / "idea-bot" / METADATA_FILE).read_text(
                encoding="utf-8"
            )
        )
        assert metadata[Keyword("trusted")] is True
        assert metadata[Keyword("author")] == "agent-42"

    def test_approve_missing_raises(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        with pytest.raises(PrototypeError, match="no staged prototype"):
            approve_prototype("ghost")

    def test_approve_conflict_with_trusted_library(self, tmp_path, monkeypatch):
        test_paths = self._stage(monkeypatch, tmp_path)
        clash = test_paths.prototypes_dir() / "idea-bot"
        clash.mkdir(parents=True)
        (clash / BEHAVIOR_FILE).write_text("{:version 1}\n", encoding="utf-8")
        with pytest.raises(PrototypeError, match="already exists"):
            approve_prototype("idea-bot")
        # Staging copy untouched on failure
        assert (test_paths.prototypes_staging_dir() / "idea-bot").exists()

    def test_reject_deletes_staging_dir(self, tmp_path, monkeypatch):
        test_paths = self._stage(monkeypatch, tmp_path)
        reject_prototype("idea-bot")
        assert not (test_paths.prototypes_staging_dir() / "idea-bot").exists()
        assert list_staging_prototypes() == []

    def test_reject_missing_raises(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        with pytest.raises(PrototypeError, match="no staged prototype"):
            reject_prototype("ghost")

    @pytest.mark.parametrize("bad_name", ["../evil", "a/b", ""])
    def test_reject_path_injection_rejected(self, tmp_path, monkeypatch, bad_name):
        _use_tmp_paths(monkeypatch, tmp_path)
        with pytest.raises(PrototypeError):
            reject_prototype(bad_name)


class TestSpawnTrustGate:
    def _plant_untrusted(self, test_paths: TlonPaths, name="rogue") -> None:
        proto_dir = test_paths.prototypes_dir() / name
        proto_dir.mkdir(parents=True)
        (proto_dir / BEHAVIOR_FILE).write_text("{:version 1}\n", encoding="utf-8")
        (proto_dir / METADATA_FILE).write_text(
            "{:name :rogue :trusted false}\n", encoding="utf-8"
        )

    async def test_untrusted_prototype_spawn_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tlon_paths = TlonPaths(home=Path(tmpdir))
            seed_prototypes(tlon_paths)
            self._plant_untrusted(tlon_paths)
            registry, _bus = await _make_registry(
                tlon_paths, {RESIDENT: AgentConfig(id=RESIDENT, name="居民甲")}
            )

            with pytest.raises(SpawnRejectedError) as exc_info:
                await registry.spawn_contractor(
                    prototype="rogue", name="捣蛋鬼", spawner_id=RESIDENT, session_id="s1"
                )
            assert exc_info.value.reason == "untrusted_prototype"

    async def test_spawn_agent_tool_reports_untrusted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tlon_paths = TlonPaths(home=Path(tmpdir))
            seed_prototypes(tlon_paths)
            self._plant_untrusted(tlon_paths)
            registry, _bus = await _make_registry(
                tlon_paths, {RESIDENT: AgentConfig(id=RESIDENT, name="居民甲")}
            )
            tool = SpawnAgentTool(registry=registry, spawner_id=RESIDENT)

            result = await tool.execute(prototype="rogue", name="捣蛋鬼")

            assert result.success is False
            assert result.data["reason"] == "untrusted_prototype"
            assert "untrusted_prototype" in result.error
            assert "mirror" in result.error

    async def test_trusted_prototype_still_spawnable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tlon_paths = TlonPaths(home=Path(tmpdir))
            seed_prototypes(tlon_paths)
            registry, _bus = await _make_registry(
                tlon_paths, {RESIDENT: AgentConfig(id=RESIDENT, name="居民甲")}
            )

            async def fake_start(agent_id: str):
                agent = MagicMock()
                agent.shutdown = AsyncMock()
                registry._agents[agent_id] = agent
                return agent

            registry._start_agent = AsyncMock(side_effect=fake_start)
            config = await registry.spawn_contractor(
                prototype="deep-thinker", name="深思", spawner_id=RESIDENT, session_id="s1"
            )
            assert config.role == "contractor"

    async def test_approved_prototype_becomes_spawnable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tlon_paths = TlonPaths(home=Path(tmpdir))
            seed_prototypes(tlon_paths)
            write_staging_prototype(
                "idea-bot", VALID_BEHAVIOR, author_id="a", tlon_paths=tlon_paths
            )
            registry, _bus = await _make_registry(
                tlon_paths, {RESIDENT: AgentConfig(id=RESIDENT, name="居民甲")}
            )

            # Staged: refused
            tool = SpawnAgentTool(registry=registry, spawner_id=RESIDENT)
            result = await tool.execute(prototype="idea-bot", name="点子")
            assert result.success is False
            # Staged prototypes aren't even in the trusted library
            assert "not found" in result.error

            approve_prototype("idea-bot", tlon_paths=tlon_paths)

            async def fake_start(agent_id: str):
                agent = MagicMock()
                agent.shutdown = AsyncMock()
                registry._agents[agent_id] = agent
                return agent

            registry._start_agent = AsyncMock(side_effect=fake_start)
            result = await tool.execute(prototype="idea-bot", name="点子")
            assert result.success is True
            assert result.data["prototype"] == "idea-bot"


class TestWritePrototypeTool:
    def test_schema(self):
        tool = WritePrototypeTool(author_id="agent-1")
        assert tool.name == "writePrototype"
        params = tool.parameters
        assert set(params["required"]) == {"name", "behavior"}
        for prop in ("display_name", "description", "identity", "soul", "rules"):
            assert prop in params["properties"]

    async def test_success_returns_pending_approval(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        tool = WritePrototypeTool(author_id="agent-1")

        result = await tool.execute(
            name="idea-bot",
            behavior=VALID_BEHAVIOR,
            display_name="点子机器",
            description="脑暴",
            identity="我是点子机器",
        )

        assert result.success is True
        assert result.data["status"] == "pending_approval"
        assert result.data["trusted"] is False
        assert "mirror" in result.message
        staged = list_staging_prototypes()
        assert [p.name for p in staged] == ["idea-bot"]
        assert staged[0].author == "agent-1"
        assert (test_paths.prototypes_staging_dir() / "idea-bot").exists()

    async def test_validation_error_is_readable_and_writes_nothing(
        self, tmp_path, monkeypatch
    ):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        tool = WritePrototypeTool(author_id="agent-1")

        result = await tool.execute(name="bad", behavior="{:loop {:max-iterations 3}}")

        assert result.success is False
        assert ":version" in result.error
        assert "hint" in result.data
        assert not test_paths.prototypes_staging_dir().exists() or not list(
            test_paths.prototypes_staging_dir().iterdir()
        )


class TestSpecSourceHash:
    def test_hash_matches_behavior_file_sha256(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        config_dir = test_paths.agent_config_dir("h-001")
        behavior_file = config_dir / "behavior.tlon"
        behavior_file.write_text(VALID_BEHAVIOR, encoding="utf-8")

        spec = AgentSpec.load("h-001")

        expected = hashlib.sha256(VALID_BEHAVIOR.encode("utf-8")).hexdigest()[:12]
        assert spec.source_hash == expected
        assert len(spec.source_hash) == 12

    def test_missing_behavior_file_refuses_boot(self, tmp_path, monkeypatch):
        _use_tmp_paths(monkeypatch, tmp_path)
        with pytest.raises(SpecValidationError, match="refused to boot"):
            AgentSpec.load("ghost-001")

    def test_incomplete_spec_file_raises(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        config_dir = test_paths.agent_config_dir("h-002")
        (config_dir / "behavior.tlon").write_text("{:version 1}\n", encoding="utf-8")
        with pytest.raises(SpecValidationError):
            AgentSpec.load("h-002")

    def test_hash_changes_with_content(self, tmp_path, monkeypatch):
        test_paths = _use_tmp_paths(monkeypatch, tmp_path)
        behavior_file = test_paths.agent_config_dir("h-003") / "behavior.tlon"
        behavior_file.write_text(VALID_BEHAVIOR, encoding="utf-8")
        first = AgentSpec.load("h-003").source_hash
        behavior_file.write_text(
            VALID_BEHAVIOR.replace(":max-iterations 10", ":max-iterations 11"),
            encoding="utf-8",
        )
        second = AgentSpec.load("h-003").source_hash
        assert first != second

    def test_session_span_carries_spec_hash(self, monkeypatch):
        recorded: dict = {}

        @contextlib.contextmanager
        def fake_span(name, source=None, attributes=None):
            recorded["name"] = name
            recorded["attributes"] = attributes
            yield MagicMock()

        monkeypatch.setattr("tlon.agents.stages.services.start_span", fake_span)
        session = SimpleNamespace(
            id="s-1", state=SimpleNamespace(value="active"), carrier={}
        )
        fake_services = SimpleNamespace(
            identity=SimpleNamespace(id="a-1"), spec=SimpleNamespace(source_hash="abc123def456")
        )

        with StageServices.session_span(fake_services, session):
            pass

        assert recorded["name"] == "agent.session"
        assert recorded["attributes"]["spec_hash"] == "abc123def456"
        assert recorded["attributes"]["session_id"] == "s-1"
