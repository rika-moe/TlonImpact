"""SpawnManager — the persisted (spawner, session) quota ledger.

Sessions survive a process restart via journal replay (ADR 0007), so the
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

import pytest

from tlon.agents.spawn import SpawnManager
from tlon.base.config import AgentConfig, LLMConfig, yaml_load_file
from tlon.base.paths import TlonPaths
from tlon.tools.agents.ports import SpawnRejectedError
from tlon.tools.prototypes.ports import PrototypeError

ECHO_EDN = """\
{:version 1
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["sink@tlon.internal"]})]}]}
"""

_SPAWNER_MODEL = "test/spawner-model"


class _FakeProvider:
    """Config provider stand-in: every spawner carries a declared model
    (ADR 0016 — the child inherits it); reloads fine."""

    def __init__(self, model: str | None = _SPAWNER_MODEL):
        self._model = model

    def get_config(self, agent_id: str) -> AgentConfig | None:
        llm = LLMConfig(model_name=self._model) if self._model is not None else None
        return AgentConfig(id=agent_id, name=agent_id, llm=llm)

    async def reload_agent(self, agent_id: str) -> bool:
        return True


class _FakeBus:
    async def publish(self, event: object) -> None:
        pass


class _FakeRegistry:
    """The registry surface SpawnManager touches, minus all assembly."""

    def __init__(self, paths: TlonPaths, provider: _FakeProvider | None = None):
        self._paths: TlonPaths | None = paths
        self._agents: dict[str, object] = {}
        self._agent_info = provider or _FakeProvider()
        self.event_bus = _FakeBus()

    def register(self, agent_id: str) -> None:
        pass

    async def _start_agent(self, agent_id: str) -> None:
        return None


def _write_prototype(p: TlonPaths, name: str) -> None:
    proto_dir = p.prototypes_dir() / name
    proto_dir.mkdir(parents=True, exist_ok=True)
    (proto_dir / "behavior.tlon").write_text(ECHO_EDN, encoding="utf-8")


class TestQuotaPersistence:
    async def test_quota_survives_restart(self, tmp_path):
        """A fresh SpawnManager over the same home keeps the spent quota —
        a restart no longer refills it."""
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]
        await manager.spawn_member("worker", "甲", "boss-1", "sess-1")
        await manager.spawn_member("worker", "乙", "boss-1", "sess-1")
        with pytest.raises(SpawnRejectedError) as exc:
            await manager.spawn_member("worker", "丙", "boss-1", "sess-1")
        assert exc.value.reason == "session_quota"
        assert p.spawn_quota_ledger_file().exists()

        # "Restart": a new manager over the same ledger file.
        restarted = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]
        with pytest.raises(SpawnRejectedError) as exc:
            await restarted.spawn_member("worker", "丙", "boss-1", "sess-1")
        assert exc.value.reason == "session_quota"
        # A different session bucket is unaffected by the restart.
        await restarted.spawn_member("worker", "丁", "boss-1", "sess-2")

    async def test_quota_counts_accumulate_across_restart(self, tmp_path):
        """Counts add up across processes: one spawn before the restart
        leaves only one spawn of headroom after it."""
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]
        await manager.spawn_member("worker", "甲", "boss-1", "sess-1")

        restarted = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]
        await restarted.spawn_member("worker", "乙", "boss-1", "sess-1")  # 2nd of 2
        with pytest.raises(SpawnRejectedError) as exc:
            await restarted.spawn_member("worker", "丙", "boss-1", "sess-1")
        assert exc.value.reason == "session_quota"

    async def test_corrupt_ledger_starts_empty(self, tmp_path):
        """An unparseable ledger warns and starts empty — never a boot blocker."""
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        p.spawn_quota_ledger_file().write_text("not json {{{", encoding="utf-8")

        manager = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]  # must not raise
        await manager.spawn_member("worker", "甲", "boss-1", "sess-1")
        # The successful spawn rewrote the file with valid content.
        json.loads(p.spawn_quota_ledger_file().read_text(encoding="utf-8"))

    async def test_malformed_ledger_shape_starts_empty(self, tmp_path):
        """Valid JSON with the wrong top-level shape starts empty too."""
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        p.spawn_quota_ledger_file().write_text('{"quota": {"not": "a list"}}', encoding="utf-8")

        manager = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]
        await manager.spawn_member("worker", "甲", "boss-1", "sess-1")

    async def test_malformed_entries_skipped_valid_kept(self, tmp_path):
        """Per-entry tolerance: bad rows are skipped, valid rows still bind."""
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        p.spawn_quota_ledger_file().write_text(
            json.dumps(
                {
                    "version": 1,
                    "quota": [
                        {"spawner": "boss-1", "session": "sess-1", "count": 2},
                        {"spawner": 1, "session": None, "count": "x"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        manager = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]
        with pytest.raises(SpawnRejectedError) as exc:
            await manager.spawn_member("worker", "丙", "boss-1", "sess-1")
        assert exc.value.reason == "session_quota"
        await manager.spawn_member("worker", "甲", "boss-2", "sess-1")


def _const_tilt(value: float):
    async def provider(prototype: str) -> float:
        return value

    return provider


class TestSpawnTilt:
    """Budget-3 quota tilt by bloodline fitness (): the effective
    per-session quota is ceil(base * tilt); tilt 1.0 is the neutral
    cold-start/failure fallback."""

    async def test_tilt_two_widens_quota(self, tmp_path):
        """tilt 2.0 over base quota 2 gives an effective quota of 4."""
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(  # type: ignore[arg-type]
            _FakeRegistry(p), 8, 3, 2, tilt_provider=_const_tilt(2.0)
        )
        for name in "甲乙丙丁":
            await manager.spawn_member("worker", name, "boss-1", "sess-1")
        with pytest.raises(SpawnRejectedError) as exc:
            await manager.spawn_member("worker", "戊", "boss-1", "sess-1")
        assert exc.value.reason == "session_quota"
        assert "tilt 2.00" in str(exc.value)

    async def test_tilt_half_narrows_quota(self, tmp_path):
        """tilt 0.5 over base quota 2 gives an effective quota of 1."""
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(  # type: ignore[arg-type]
            _FakeRegistry(p), 8, 3, 2, tilt_provider=_const_tilt(0.5)
        )
        await manager.spawn_member("worker", "甲", "boss-1", "sess-1")
        with pytest.raises(SpawnRejectedError) as exc:
            await manager.spawn_member("worker", "乙", "boss-1", "sess-1")
        assert exc.value.reason == "session_quota"
        assert "tilt 0.50" in str(exc.value)

    async def test_provider_failure_falls_back_to_neutral(self, tmp_path):
        """A raising tilt provider is fail-open: the untilted quota (2)
        still binds, so fitness bookkeeping never blocks a spawn."""

        async def _boom(prototype: str) -> float:
            raise OSError("fitness derivation broke")

        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(  # type: ignore[arg-type]
            _FakeRegistry(p), 8, 3, 2, tilt_provider=_boom
        )
        await manager.spawn_member("worker", "甲", "boss-1", "sess-1")
        await manager.spawn_member("worker", "乙", "boss-1", "sess-1")
        with pytest.raises(SpawnRejectedError) as exc:
            await manager.spawn_member("worker", "丙", "boss-1", "sess-1")
        assert exc.value.reason == "session_quota"
        assert "tilt 1.00" in str(exc.value)


def _const_solvency(value: bool):
    async def provider(prototype: str) -> bool:
        return value

    return provider


class TestSpawnSolvency:
    """Solvency gate (): an insolvent bloodline cannot spawn; the
    check sits after the trust gate and fails open."""

    async def test_insolvent_lineage_rejected(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(  # type: ignore[arg-type]
            _FakeRegistry(p), 8, 3, 2, solvency=_const_solvency(False)
        )
        with pytest.raises(SpawnRejectedError) as exc:
            await manager.spawn_member("worker", "甲", "boss-1", "sess-1")
        assert exc.value.reason == "insolvent_lineage"

    async def test_solvent_lineage_spawns(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(  # type: ignore[arg-type]
            _FakeRegistry(p), 8, 3, 2, solvency=_const_solvency(True)
        )
        await manager.spawn_member("worker", "甲", "boss-1", "sess-1")

    async def test_provider_failure_fails_open(self, tmp_path):
        """A raising solvency provider never blocks a spawn."""

        async def _boom(prototype: str) -> bool:
            raise OSError("fitness derivation broke")

        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(  # type: ignore[arg-type]
            _FakeRegistry(p), 8, 3, 2, solvency=_boom
        )
        await manager.spawn_member("worker", "甲", "boss-1", "sess-1")


def _const_probation(value: bool):
    async def provider(spawner_id: str) -> bool:
        return value

    return provider


class TestSpawnProbation:
    """Probation quota cap (, trust ladder): keyed by *spawner id*
    (unlike tilt/solvency, which key on the target prototype) — a
    probationary spawner's effective per-session quota is capped at 1,
    whatever the tilt says. Fail-open like the other two providers."""

    async def test_probation_spawner_capped_at_one(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(  # type: ignore[arg-type]
            _FakeRegistry(p), 8, 3, 2, probation=_const_probation(True)
        )
        await manager.spawn_member("worker", "甲", "boss-1", "sess-1")
        with pytest.raises(SpawnRejectedError) as exc:
            await manager.spawn_member("worker", "乙", "boss-1", "sess-1")
        assert exc.value.reason == "session_quota"
        assert "probation" in str(exc.value)

    async def test_probation_cap_wins_over_tilt(self, tmp_path):
        """tilt 2.0 would widen the quota to 4; probation caps it at 1 —
        the tilt judges the target bloodline, probation judges the spawner."""
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(  # type: ignore[arg-type]
            _FakeRegistry(p), 8, 3, 2,
            tilt_provider=_const_tilt(2.0),
            probation=_const_probation(True),
        )
        await manager.spawn_member("worker", "甲", "boss-1", "sess-1")
        with pytest.raises(SpawnRejectedError) as exc:
            await manager.spawn_member("worker", "乙", "boss-1", "sess-1")
        assert exc.value.reason == "session_quota"
        assert "probation" in str(exc.value)

    async def test_full_spawner_uncapped(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(  # type: ignore[arg-type]
            _FakeRegistry(p), 8, 3, 2, probation=_const_probation(False)
        )
        await manager.spawn_member("worker", "甲", "boss-1", "sess-1")
        await manager.spawn_member("worker", "乙", "boss-1", "sess-1")
        with pytest.raises(SpawnRejectedError) as exc:
            await manager.spawn_member("worker", "丙", "boss-1", "sess-1")
        assert exc.value.reason == "session_quota"
        assert "probation" not in str(exc.value)

    async def test_provider_failure_fails_open(self, tmp_path):
        """A raising probation provider reads as *not* probation — trust
        bookkeeping must never tighten a quota."""

        async def _boom(spawner_id: str) -> bool:
            raise OSError("trust derivation broke")

        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(  # type: ignore[arg-type]
            _FakeRegistry(p), 8, 3, 2, probation=_boom
        )
        await manager.spawn_member("worker", "甲", "boss-1", "sess-1")
        await manager.spawn_member("worker", "乙", "boss-1", "sess-1")


class TestModelInheritance:
    """ADR 0016: the child member inherits the spawner's model — the
    cost/capability profile follows the bloodline (ADR 0014). No
    process-level fallback: a spawner without a declared model fails the
    spawn at instantiation."""

    async def test_child_inherits_spawner_model(self, tmp_path):
        p = TlonPaths(home=tmp_path)
        _write_prototype(p, "worker")
        manager = SpawnManager(_FakeRegistry(p), 8, 3, 2)  # type: ignore[arg-type]

        config = await manager.spawn_member("worker", "甲", "boss-1", "sess-1")

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
            await manager.spawn_member("worker", "甲", "boss-1", "sess-1")
