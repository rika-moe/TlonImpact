"""Tests for EpisodicMemoryStore."""

import json
import os
from pathlib import Path

import pytest

from tlon.base.paths import TlonPaths
from tlon.memory.episodic_store import EpisodicMemoryStore


@pytest.fixture
def isolated_paths(tmp_path: Path) -> TlonPaths:
    """A temp TlonPaths; its memory dir is the stores' storage_path."""
    return TlonPaths(home=tmp_path)


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
        store = EpisodicMemoryStore("test-agent", isolated_paths.agent_memory_dir("test-agent"))
        await store.load()
        await store.add_fact(_make_fact("f-001", "old version"))
        await store.add_fact(_make_fact("f-002", "new version"))

        # Simulate restart: fresh instance replays the JSONL file.
        reloaded = EpisodicMemoryStore("test-agent", isolated_paths.agent_memory_dir("test-agent"))
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
        store = EpisodicMemoryStore("test-agent", isolated_paths.agent_memory_dir("test-agent"))
        await store.load()
        await store.add_fact(_make_fact("f-001"))
        other = _make_fact("f-002")
        other["object"] = "tea"
        await store.add_fact(other)

        reloaded = EpisodicMemoryStore("test-agent", isolated_paths.agent_memory_dir("test-agent"))
        await reloaded.load()

        active = await reloaded.get_all_facts()
        assert {f["id"] for f in active} == {"f-001", "f-002"}


def _make_summary(summary_id: str, text: str = "some summary") -> dict:
    return {"id": summary_id, "summary_text": text, "keywords": ["test"]}


class TestAtomicPersist:
    """Regression tests: _persist_* must never truncate the existing file on crash."""

    async def test_summaries_persist_replace_crash_keeps_original(
        self, isolated_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If os.replace fails, the original summaries file stays intact."""
        store = EpisodicMemoryStore("test-agent", isolated_paths.agent_memory_dir("test-agent"))
        await store.load()
        await store.add_summary(_make_summary("s-001"))
        original = store.summaries_file.read_bytes()
        assert original

        def boom(src: str, dst: str) -> None:
            raise OSError("simulated crash")

        monkeypatch.setattr(os, "replace", boom)
        assert await store.delete_summary("s-001") is True

        assert store.summaries_file.read_bytes() == original
        assert not store.summaries_file.with_name(store.summaries_file.name + ".tmp").exists()

    async def test_summaries_persist_write_crash_keeps_original(
        self, isolated_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If serialization fails mid-write, the original file stays intact."""
        store = EpisodicMemoryStore("test-agent", isolated_paths.agent_memory_dir("test-agent"))
        await store.load()
        await store.add_summary(_make_summary("s-001"))
        await store.add_summary(_make_summary("s-002", "another"))
        original = store.summaries_file.read_bytes()

        def boom(*args: object, **kwargs: object) -> str:
            raise OSError("simulated crash")

        monkeypatch.setattr(json, "dumps", boom)
        # Deleting s-001 leaves s-002 in cache, so the rewrite actually
        # reaches the (failing) serialization of a remaining item.
        assert await store.delete_summary("s-001") is True

        assert store.summaries_file.read_bytes() == original
        assert not store.summaries_file.with_name(store.summaries_file.name + ".tmp").exists()

    async def test_summaries_persist_normal_path(self, isolated_paths: TlonPaths) -> None:
        """Normal persist rewrites the file and leaves no temp file behind."""
        store = EpisodicMemoryStore("test-agent", isolated_paths.agent_memory_dir("test-agent"))
        await store.load()
        await store.add_summary(_make_summary("s-001"))
        await store.add_summary(_make_summary("s-002", "another"))
        assert await store.delete_summary("s-001") is True

        assert not store.summaries_file.with_name(store.summaries_file.name + ".tmp").exists()

        reloaded = EpisodicMemoryStore("test-agent", isolated_paths.agent_memory_dir("test-agent"))
        await reloaded.load()
        summaries = await reloaded.get_all_summaries()
        assert [s["id"] for s in summaries] == ["s-002"]

    async def test_facts_persist_replace_crash_keeps_original(
        self, isolated_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If os.replace fails, the original facts file stays intact."""
        store = EpisodicMemoryStore("test-agent", isolated_paths.agent_memory_dir("test-agent"))
        await store.load()
        await store.add_fact(_make_fact("f-001"))
        original = store.facts_file.read_bytes()
        assert original

        def boom(src: str, dst: str) -> None:
            raise OSError("simulated crash")

        monkeypatch.setattr(os, "replace", boom)
        assert await store.delete_fact("f-001") is True

        assert store.facts_file.read_bytes() == original
        assert not store.facts_file.with_name(store.facts_file.name + ".tmp").exists()
