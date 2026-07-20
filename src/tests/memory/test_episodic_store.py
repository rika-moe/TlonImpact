"""Tests for EpisodicMemoryStore."""

from pathlib import Path

import pytest

import tlon.memory.episodic_store as episodic_store_module
from tlon.base.paths import TlonPaths
from tlon.memory.episodic_store import EpisodicMemoryStore


@pytest.fixture
def isolated_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TlonPaths:
    """Point the module-level paths singleton at a temp dir."""
    test_paths = TlonPaths(home=tmp_path)
    monkeypatch.setattr(episodic_store_module, "paths", test_paths)
    return test_paths


def _make_fact(fact_id: str, description: str = "some fact") -> dict:
    return {
        "id": fact_id,
        "subject": "alice",
        "predicate": "likes",
        "object": "coffee",
        "description": description,
    }


class TestFactSupersedePersistence:
    """Regression tests: superseded_by must survive a reload (restart)."""

    async def test_superseded_fact_stays_superseded_after_reload(
        self, isolated_paths: TlonPaths
    ) -> None:
        """Adding a fact with the same triple supersedes the old one on disk."""
        store = EpisodicMemoryStore("test-agent", Path("ignored"))
        await store.load()
        await store.add_fact(_make_fact("f-001", "old version"))
        await store.add_fact(_make_fact("f-002", "new version"))

        # Simulate restart: fresh instance replays the JSONL file.
        reloaded = EpisodicMemoryStore("test-agent", Path("ignored"))
        await reloaded.load()

        old_fact = await reloaded.get_fact("f-001")
        assert old_fact is not None
        assert old_fact.get("superseded_by") == "f-002"

        active = await reloaded.get_recent_facts()
        assert [f["id"] for f in active] == ["f-002"]

        all_facts = await reloaded.get_all_facts()
        assert [f["id"] for f in all_facts] == ["f-002"]

    async def test_non_superseded_facts_still_appended(self, isolated_paths: TlonPaths) -> None:
        """Facts with distinct triples remain active after reload."""
        store = EpisodicMemoryStore("test-agent", Path("ignored"))
        await store.load()
        await store.add_fact(_make_fact("f-001"))
        other = _make_fact("f-002")
        other["object"] = "tea"
        await store.add_fact(other)

        reloaded = EpisodicMemoryStore("test-agent", Path("ignored"))
        await reloaded.load()

        active = await reloaded.get_all_facts()
        assert {f["id"] for f in active} == {"f-001", "f-002"}
