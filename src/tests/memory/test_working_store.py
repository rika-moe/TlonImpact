"""Tests for WorkingMemoryStore."""

import os
from pathlib import Path

import pytest

from tlon.base.paths import TlonPaths
from tlon.memory.working_store import WorkingMemoryStore


@pytest.fixture
def isolated_paths(tmp_path: Path) -> TlonPaths:
    """A temp TlonPaths; its memory dir is the stores' storage_path."""
    return TlonPaths(home=tmp_path)


def _make_interaction(interaction_id: str, text: str = "hello") -> dict:
    return {"id": interaction_id, "user_message": text, "agent_response": "hi"}


class TestAtomicPersist:
    """Regression tests: _persist must never truncate the existing file on crash."""

    async def test_persist_replace_crash_keeps_original(
        self, isolated_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If os.replace fails, the original interactions file stays intact."""
        store = WorkingMemoryStore("test-agent", isolated_paths.agent_memory_dir("test-agent"))
        await store.load()
        await store.add(_make_interaction("i-001"))
        original = store.interactions_file.read_bytes()
        assert original

        def boom(src: str, dst: str) -> None:
            raise OSError("simulated crash")

        monkeypatch.setattr(os, "replace", boom)
        assert await store.delete("i-001") is True

        assert store.interactions_file.read_bytes() == original
        tmp_file = store.interactions_file.with_name(store.interactions_file.name + ".tmp")
        assert not tmp_file.exists()

    async def test_persist_normal_path(self, isolated_paths: TlonPaths) -> None:
        """Normal persist rewrites the file and leaves no temp file behind."""
        store = WorkingMemoryStore("test-agent", isolated_paths.agent_memory_dir("test-agent"))
        await store.load()
        await store.add(_make_interaction("i-001"))
        await store.add(_make_interaction("i-002", "again"))
        assert await store.delete("i-001") is True

        tmp_file = store.interactions_file.with_name(store.interactions_file.name + ".tmp")
        assert not tmp_file.exists()

        reloaded = WorkingMemoryStore("test-agent", isolated_paths.agent_memory_dir("test-agent"))
        await reloaded.load()
        interactions = await reloaded.get_all()
        assert [i["id"] for i in interactions] == ["i-002"]
