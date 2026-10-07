"""Tests for ClaimStore (ADR 0017)."""

import json
import os
from pathlib import Path

import pytest

from tlon.memory.claim_store import ClaimStore
from tlon.memory.models import Claim


def _store(tmp_path: Path) -> ClaimStore:
    return ClaimStore("test-agent", tmp_path / "memory")


def _make_claim(claim_id: str, text: str = "some claim", **overrides: object) -> dict:
    data = Claim(
        id=claim_id,
        text=text,
        timestamp="2026-09-17T10:00:00",
        valid_from="2026-09-17T10:00:00",
    ).to_dict()
    data.update(overrides)
    return data


class TestClaimLifecycle:
    async def test_add_get_and_reload(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await store.load()
        await store.add_claim(_make_claim("c-001", "用户偏好深色主题"))
        await store.add_claim(_make_claim("c-002", "项目使用 Python"))

        # Simulate restart: a fresh instance replays the JSONL file.
        reloaded = _store(tmp_path)
        await reloaded.load()

        claim = await reloaded.get_claim("c-001")
        assert claim is not None
        assert claim["text"] == "用户偏好深色主题"
        assert [c["id"] for c in await reloaded.get_all_claims()] == ["c-001", "c-002"]

    async def test_recent_claims_exclude_superseded(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await store.load()
        await store.add_claim(_make_claim("c-001", status="superseded", superseded_by="c-002"))
        await store.add_claim(_make_claim("c-002"))

        assert [c["id"] for c in await store.get_recent_claims()] == ["c-002"]
        assert [c["id"] for c in await store.get_all_claims()] == ["c-002"]
        assert [c["id"] for c in await store.get_all_claims(include_superseded=True)] == [
            "c-001",
            "c-002",
        ]

    async def test_recent_claims_limit_takes_tail(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await store.load()
        for i in range(5):
            await store.add_claim(_make_claim(f"c-{i:03d}"))

        assert [c["id"] for c in await store.get_recent_claims(2)] == ["c-003", "c-004"]

    async def test_delete_claim_persists(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await store.load()
        await store.add_claim(_make_claim("c-001"))
        await store.add_claim(_make_claim("c-002"))

        assert await store.delete_claim("c-001") is True
        assert await store.delete_claim("no-such") is False

        reloaded = _store(tmp_path)
        await reloaded.load()
        assert [c["id"] for c in await reloaded.get_all_claims()] == ["c-002"]

    async def test_add_claim_requires_id(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await store.load()
        claim = _make_claim("c-001")
        del claim["id"]
        with pytest.raises(ValueError):
            await store.add_claim(claim)

    async def test_bad_line_skipped_on_load(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await store.load()
        await store.add_claim(_make_claim("c-001"))
        with store.claims_file.open("a", encoding="utf-8") as f:
            f.write("{not json\n")

        reloaded = _store(tmp_path)
        await reloaded.load()
        assert [c["id"] for c in await reloaded.get_all_claims()] == ["c-001"]


class TestSupersede:
    async def test_supersede_marks_in_place_without_deleting(self, tmp_path: Path) -> None:
        """失效不删除: the old claim stays in the file for audit; only
        its lifecycle fields change, and it drops out of active views."""
        store = _store(tmp_path)
        await store.load()
        await store.add_claim(_make_claim("c-old", "用户喜欢手冲咖啡"))
        await store.add_claim(_make_claim("c-new", "用户改喝美式"))

        assert await store.supersede_claim("c-old", "c-new", "2026-09-17T12:00:00") is True

        old = await store.get_claim("c-old")
        assert old is not None
        assert old["status"] == "superseded"
        assert old["superseded_by"] == "c-new"
        assert old["valid_to"] == "2026-09-17T12:00:00"
        # Active views exclude it; the audit view keeps both.
        assert [c["id"] for c in await store.get_all_claims()] == ["c-new"]
        assert [c["id"] for c in await store.get_recent_claims()] == ["c-new"]
        assert [c["id"] for c in await store.get_all_claims(include_superseded=True)] == [
            "c-old",
            "c-new",
        ]

    async def test_supersede_persists_across_restart(self, tmp_path: Path) -> None:
        """The mutation rewrites the file atomically, so a fresh instance
        replays the superseded state."""
        store = _store(tmp_path)
        await store.load()
        await store.add_claim(_make_claim("c-old"))
        await store.add_claim(_make_claim("c-new"))
        await store.supersede_claim("c-old", "c-new", "2026-09-17T12:00:00")

        reloaded = _store(tmp_path)
        await reloaded.load()
        old = await reloaded.get_claim("c-old")
        assert old is not None
        assert old["status"] == "superseded"
        assert old["superseded_by"] == "c-new"
        assert [c["id"] for c in await reloaded.get_all_claims()] == ["c-new"]

    async def test_supersede_unknown_id_changes_nothing(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await store.load()
        await store.add_claim(_make_claim("c-001"))
        original = store.claims_file.read_bytes()

        assert await store.supersede_claim("no-such", "c-001", "2026-09-17T12:00:00") is False

        claim = await store.get_claim("c-001")
        assert claim is not None and claim["status"] == "active"
        assert store.claims_file.read_bytes() == original


class TestKeywordSearch:
    async def test_search_matches_active_claim_text(self, tmp_path: Path) -> None:
        store = _store(tmp_path)
        await store.load()
        await store.add_claim(_make_claim("c-001", "用户喜欢浓缩咖啡"))
        await store.add_claim(_make_claim("c-002", "项目部署在东京"))
        await store.add_claim(
            _make_claim("c-003", "旧的咖啡偏好", status="superseded", superseded_by="c-001")
        )

        results = await store.search_claims_by_keyword(["咖啡"], 10)
        assert [c["id"] for c in results] == ["c-001"]

        assert await store.search_claims_by_keyword(["无匹配词"], 10) == []


class TestAtomicPersist:
    """Regression tests: _persist must never truncate the existing file on crash."""

    async def test_persist_replace_crash_keeps_original(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If os.replace fails, the original claims file stays intact."""
        store = _store(tmp_path)
        await store.load()
        await store.add_claim(_make_claim("c-001"))
        original = store.claims_file.read_bytes()
        assert original

        def boom(src: str, dst: str) -> None:
            raise OSError("simulated crash")

        monkeypatch.setattr(os, "replace", boom)
        assert await store.delete_claim("c-001") is True

        assert store.claims_file.read_bytes() == original
        assert not store.claims_file.with_name(store.claims_file.name + ".tmp").exists()

    async def test_persist_write_crash_keeps_original(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If serialization fails mid-write, the original file stays intact."""
        store = _store(tmp_path)
        await store.load()
        await store.add_claim(_make_claim("c-001"))
        await store.add_claim(_make_claim("c-002", "another"))
        original = store.claims_file.read_bytes()

        def boom(*args: object, **kwargs: object) -> str:
            raise OSError("simulated crash")

        monkeypatch.setattr(json, "dumps", boom)
        # Deleting c-001 leaves c-002 in cache, so the rewrite actually
        # reaches the (failing) serialization of a remaining item.
        assert await store.delete_claim("c-001") is True

        assert store.claims_file.read_bytes() == original
        assert not store.claims_file.with_name(store.claims_file.name + ".tmp").exists()

    async def test_persist_normal_path(self, tmp_path: Path) -> None:
        """Normal persist rewrites the file and leaves no temp file behind."""
        store = _store(tmp_path)
        await store.load()
        await store.add_claim(_make_claim("c-001"))
        await store.add_claim(_make_claim("c-002", "another"))
        assert await store.delete_claim("c-001") is True

        assert not store.claims_file.with_name(store.claims_file.name + ".tmp").exists()

        reloaded = _store(tmp_path)
        await reloaded.load()
        assert [c["id"] for c in await reloaded.get_all_claims()] == ["c-002"]
