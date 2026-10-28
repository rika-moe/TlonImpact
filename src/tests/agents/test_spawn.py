"""SpawnManager — the persisted (spawner, run) quota ledger.

Runs survive a process restart via journal replay (ADR 0007), so the
anti-runaway spawn quota must survive it too: the ledger lives at
``~/.tlon/shared/spawn-quota.json`` (atomic replace) and a fresh
SpawnManager over the same home picks up the previous process's counts.
Corrupt ledger data warns and starts empty — a broken ledger must never
block the server from booting.

Model inheritance (ADR 0016): the child member runs the spawner's model
(``config.llm.model_name``) — no process-level fallback exists, so a
spawner without a declared model fails the spawn at instantiation.
"""

import json
from pathlib import Path
from uuid import UUID

import pytest

from tlon.agents.prototypes.management import write_trusted_prototype
from tlon.agents.spawn import SpawnManager
from tlon.base.config import AgentConfig, LLMConfig, yaml_load_file
from tlon.base.paths import TlonPaths
from tlon.evolution.ports import PrototypeError
from tlon.lifecycle.ports import SpawnRejectedError

_FIXTURE_MODULES = {
    "{:version 2 :handlers {:mail-delivered work.fixture6bdb126e/handle0}}\n": {
        "work/fixture6bdb126e.tlon": '(ns work.fixture6bdb126e (:export handle0))\n(defn handle0 [event] (let [msg (get event :mail) self (get event :self)] (let [action0 (mail-forward {:mail_id (mail-field msg :id) :to ["sink@tlon.internal"]})] {:result nil})))\n'
    }
}


ECHO_EDN = "{:version 2 :handlers {:mail-delivered work.fixture6bdb126e/handle0}}\n"

_SPAWNER_MODEL = "test/spawner-model"


class _FakeProvider:
    """Config provider stand-in: every spawner carries a declared model
    (ADR 0016 — the child inherits it); reloads fine."""

    def __init__(self, model: str | None = _SPAWNER_MODEL):
        self._model = model
        self.reloaded: list[str] = []

    def get_config(self, agent_id: str) -> AgentConfig | None:
        llm = LLMConfig(model_name=self._model) if self._model is not None else None
        return AgentConfig(id=agent_id, name=agent_id, llm=llm)

    async def reload_agent(self, agent_id: str) -> bool:
        self.reloaded.append(agent_id)
        return True


class _FakeBus:
    async def publish(self, event: object) -> None:
        pass


class _FakeRegistry:
    """The registry surface SpawnManager touches, minus all assembly."""

    def __init__(self, paths: TlonPaths, provider: _FakeProvider | None = None):
        self._paths: TlonPaths | None = paths
        self._agents: dict[str, object] = {}
        self._registered_ids: set[str] = set()
        self._profiles: dict[str, object] = {}
        self._agent_info = provider or _FakeProvider()
        self.event_bus = _FakeBus()

    def get_all_ids(self) -> list[str]:
        return list(self._registered_ids)

    def register(self, agent_id: str) -> None:
        pass

    async def _start_agent(self, agent_id: str) -> None:
        return None


def _write_prototype(p: TlonPaths, name: str) -> None:
    created = write_trusted_prototype(
        ECHO_EDN, modules=_FIXTURE_MODULES[ECHO_EDN], display_name="relay", tlon_paths=p
    )
    (p.prototypes_dir() / created.name).rename(p.prototypes_dir() / name)


class TestQuotaPersistence:
    @pytest.mark.parametrize("run_id", ["", "   "])
    async def test_spawn_requires_nonempty_run(self, tmp_path, run_id: str) -> None:
        p = TlonPaths(home=tmp_path)
        manager = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]
        with pytest.raises(SpawnRejectedError) as failure:
            await manager.spawn_member("worker", "甲", "boss-1", run_id)
        assert failure.value.reason == "invalid_run"
        assert not p.spawn_quota_ledger_file().exists()

    async def test_quota_survives_restart(self, tmp_path):
        """A fresh SpawnManager over the same home keeps the spent quota —
        a restart no longer refills it."""
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]
        await manager.spawn_member("worker", "甲", "boss-1", "run-1")
        await manager.spawn_member("worker", "乙", "boss-1", "run-1")
        with pytest.raises(SpawnRejectedError) as exc:
            await manager.spawn_member("worker", "丙", "boss-1", "run-1")
        assert exc.value.reason == "run_quota"
        assert p.spawn_quota_ledger_file().exists()

        # "Restart": a new manager over the same ledger file.
        restarted = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]
        with pytest.raises(SpawnRejectedError) as exc:
            await restarted.spawn_member("worker", "丙", "boss-1", "run-1")
        assert exc.value.reason == "run_quota"
        # A different run bucket is unaffected by the restart.
        await restarted.spawn_member("worker", "丁", "boss-1", "run-2")

    async def test_quota_counts_accumulate_across_restart(self, tmp_path):
        """Counts add up across processes: one spawn before the restart
        leaves only one spawn of headroom after it."""
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]
        await manager.spawn_member("worker", "甲", "boss-1", "run-1")

        restarted = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]
        await restarted.spawn_member("worker", "乙", "boss-1", "run-1")  # 2nd of 2
        with pytest.raises(SpawnRejectedError) as exc:
            await restarted.spawn_member("worker", "丙", "boss-1", "run-1")
        assert exc.value.reason == "run_quota"

    async def test_old_session_ledger_is_not_run_quota(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        ledger = p.spawn_quota_ledger_file()
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(
            json.dumps(
                {"version": 1, "quota": [{"spawner": "boss-1", "session": "run-1", "count": 99}]}
            ),
            encoding="utf-8",
        )
        manager = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]
        await manager.spawn_member("worker", "甲", "boss-1", "run-1")
        entries = json.loads(ledger.read_text(encoding="utf-8"))["quota"]
        assert entries == [{"spawner": "boss-1", "run": "run-1", "count": 1}]

    async def test_corrupt_ledger_starts_empty(self, tmp_path):
        """An unparseable ledger warns and starts empty — never a boot blocker."""
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        ledger = p.spawn_quota_ledger_file()
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("not json {{{", encoding="utf-8")

        manager = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]  # must not raise
        await manager.spawn_member("worker", "甲", "boss-1", "run-1")
        # The successful spawn rewrote the file with valid content.
        json.loads(p.spawn_quota_ledger_file().read_text(encoding="utf-8"))

    async def test_malformed_ledger_shape_starts_empty(self, tmp_path):
        """Valid JSON with the wrong top-level shape starts empty too."""
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        ledger = p.spawn_quota_ledger_file()
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text('{"quota": {"not": "a list"}}', encoding="utf-8")

        manager = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]
        await manager.spawn_member("worker", "甲", "boss-1", "run-1")

    async def test_malformed_entries_skipped_valid_kept(self, tmp_path):
        """Per-entry tolerance: bad rows are skipped, valid rows still bind."""
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        ledger = p.spawn_quota_ledger_file()
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(
            json.dumps(
                {
                    "version": 1,
                    "quota": [
                        {"spawner": "boss-1", "run": "run-1", "count": 2},
                        {"spawner": 1, "run": None, "count": "x"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        manager = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]
        with pytest.raises(SpawnRejectedError) as exc:
            await manager.spawn_member("worker", "丙", "boss-1", "run-1")
        assert exc.value.reason == "run_quota"
        await manager.spawn_member("worker", "甲", "boss-2", "run-1")


class TestResourceLimits:
    @pytest.mark.parametrize("spawned_by", [None, "creator"])
    async def test_origin_without_endorsements_uses_fixed_quota(
        self, tmp_path: Path, spawned_by: str | None
    ) -> None:
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")

        class Provider(_FakeProvider):
            def get_config(self, agent_id: str) -> AgentConfig:
                return AgentConfig(
                    id=agent_id,
                    name=agent_id,
                    spawned_by=spawned_by,
                    spawn_depth=1 if spawned_by else 0,
                    llm=LLMConfig(model_name=_SPAWNER_MODEL),
                )

        # No mail or endorsement records exist in this society.
        manager = SpawnManager(_FakeRegistry(p, Provider()), 8, 3, 2)  # type: ignore[arg-type]
        for name in ["first", "second"]:
            child = await manager.spawn_member("worker", name, "boss-1", "run-1")
            assert child.spawned_by == "boss-1"
            assert child.spawn_depth == (2 if spawned_by else 1)
        with pytest.raises(SpawnRejectedError) as failure:
            await manager.spawn_member("worker", "third", "boss-1", "run-1")
        assert failure.value.reason == "run_quota"

    async def test_untrusted_prototype_rejected(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        metadata = p.prototypes_dir() / "worker" / "prototype.tlon"
        metadata.write_text(
            metadata.read_text(encoding="utf-8").replace(":trusted true", ":trusted false"),
            encoding="utf-8",
        )
        manager = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]
        with pytest.raises(SpawnRejectedError) as failure:
            await manager.spawn_member("worker", "child", "boss-1", "run-1")
        assert failure.value.reason == "untrusted_prototype"
        assert not p.spawn_quota_ledger_file().exists()

    async def test_global_activation_limit(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        registry = _FakeRegistry(p)
        registry._agents["active"] = object()
        manager = SpawnManager(registry, 1, 3, 2)  # type: ignore[arg-type]
        with pytest.raises(SpawnRejectedError) as failure:
            await manager.spawn_member("worker", "child", "boss-1", "run-1")
        assert failure.value.reason == "max_activations"
        assert not p.spawn_quota_ledger_file().exists()

    async def test_depth_limit(self, tmp_path: Path) -> None:
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(_FakeRegistry(p), 8, 0, 2)  # type: ignore[arg-type]
        with pytest.raises(SpawnRejectedError) as failure:
            await manager.spawn_member("worker", "child", "boss-1", "run-1")
        assert failure.value.reason == "max_spawn_depth"
        assert not p.spawn_quota_ledger_file().exists()


class TestModelInheritance:
    """ADR 0016: the child member inherits the spawner's model — the
    cost/capability profile follows the bloodline (ADR 0014). No
    process-level fallback: a spawner without a declared model fails the
    spawn at instantiation."""

    async def test_child_inherits_spawner_model(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]

        config = await manager.spawn_member("worker", "甲", "boss-1", "run-1")

        assert config.id.startswith("m-")
        assert UUID(hex=config.id[2:]).version == 4
        assert "worker" not in config.id
        assert config.llm is not None
        assert config.llm.model_name == _SPAWNER_MODEL
        # persisted: agent.yaml on disk carries the inherited model
        on_disk = yaml_load_file(p.agent_config_file(config.id))
        assert on_disk is not None and on_disk.llm is not None
        assert on_disk.llm.model_name == _SPAWNER_MODEL

    async def test_spawner_without_model_fails(self, tmp_path):
        """No fallback: the spawn errors out instead of silently picking
        a process-level default."""
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(  # type: ignore[arg-type]
            _FakeRegistry(p, _FakeProvider(model=None)), 8, 3, 2
        )

        with pytest.raises(PrototypeError, match="model"):
            await manager.spawn_member("worker", "甲", "boss-1", "run-1")


class TestAdoptAgent:
    """Hot adoption of an out-of-process creation/edit (stage 6b)."""

    async def test_known_agent_config_reloaded_not_restarted(self, tmp_path):
        """already-known: the agent is not (re)started, but an out-of-process
        agent.yaml edit (e.g. a WebUI member edit setting llm.model_name)
        must reach the provider cache — otherwise the next start assembles
        the stale config and fails again (a start-failed member stays
        registered, so this branch is exactly what its repair path hits)."""
        p = TlonPaths(home=tmp_path)
        provider = _FakeProvider()
        registry = _FakeRegistry(p, provider)
        registry._registered_ids.add("m-1")
        registry._profiles["m-1"] = object()
        manager = SpawnManager(registry, 8, 3, 2)  # type: ignore[arg-type]

        result = await manager.adopt_agent("m-1")

        assert result == "already-known"
        assert provider.reloaded == ["m-1"]
        assert "m-1" not in registry._profiles
