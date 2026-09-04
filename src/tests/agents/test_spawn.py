"""SpawnManager — the persisted (spawner, session) quota ledger.

Sessions survive a process restart via journal replay (ADR 0007), so the
anti-runaway spawn quota must survive it too: the ledger lives at
``~/.tlon/shared/spawn-quota.json`` (atomic replace) and a fresh
SpawnManager over the same home picks up the previous process's counts.
Corrupt ledger data warns and starts empty — a broken ledger must never
block the server from booting.
"""

import json

import pytest

from tlon.agents.spawn import SpawnManager
from tlon.base.config import AgentConfig
from tlon.base.paths import TlonPaths
from tlon.tools.agents.ports import SpawnRejectedError

ECHO_EDN = """\
{:version 1
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["sink@tlon.internal"]})]}]}
"""


class _FakeProvider:
    """Config provider stand-in: unknown spawner (depth 0), reloads fine."""

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return None

    async def reload_agent(self, agent_id: str) -> bool:
        return True


class _FakeBus:
    async def publish(self, event: object) -> None:
        pass


class _FakeRegistry:
    """The registry surface SpawnManager touches, minus all assembly."""

    def __init__(self, paths: TlonPaths):
        self._paths: TlonPaths | None = paths
        self._agents: dict[str, object] = {}
        self._agent_info = _FakeProvider()
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
