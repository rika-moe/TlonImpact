"""Claim store: append-only JSONL persistence for memory claims (ADR 0017)."""

import asyncio
import contextlib
import json
import os
from pathlib import Path
from typing import Any, cast

import aiofiles

from tlon.base.logger import get_logger

logger = get_logger(__name__)


class ClaimStore:
    """Append-only store for memory claims.

    Storage format: JSON Lines (``claims.jsonl``), one claim per line.
    Claims have no capacity cap — supersession marks, never deletes
    (same storage philosophy as the session journal). Deletion exists
    only for the agent-initiated ``forget``/reflection paths and rewrites
    the file atomically (temp file + os.replace).

    Example:
        >>> store = ClaimStore("dev-001", Path("./data/agents/dev-001/memory"))
        >>> await store.load()
        >>> claim_id = await store.add_claim(claim_dict)
        >>> recent = await store.get_recent_claims(limit=10)
    """

    def __init__(self, agent_id: str, storage_path: Path):
        """Initialize the claim store.

        Args:
            agent_id: The agent ID
            storage_path: The memory directory (``{home}/{agent_id}/memory``);
                claims live in ``{storage_path}/claims.jsonl``
        """
        self.agent_id = agent_id
        self.claims_file = storage_path / "claims.jsonl"
        self._claims_cache: list[dict[str, Any]] = []
        self._loaded = False
        self._lock = asyncio.Lock()

    async def _ensure_dir(self) -> None:
        """Ensure storage directory exists."""
        self.claims_file.parent.mkdir(parents=True, exist_ok=True)

    async def load(self) -> None:
        """Load claims from disk. Should be called once at startup."""
        await self._ensure_dir()

        if self.claims_file.exists():
            try:
                self._claims_cache = []
                async with aiofiles.open(self.claims_file, encoding="utf-8") as f:
                    async for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            self._claims_cache.append(json.loads(line))
                        except json.JSONDecodeError as e:
                            logger.warning("claim_line_parse_failed", error=str(e))
                logger.info(
                    "claims_loaded",
                    agent_id=self.agent_id,
                    claim_count=len(self._claims_cache),
                )
            except Exception as e:
                logger.error("claims_load_failed", error=str(e), exc_info=True)
                self._claims_cache = []
        else:
            logger.info("claims_not_found", agent_id=self.agent_id)
            self._claims_cache = []

        self._loaded = True

    async def _persist(self) -> None:
        """Rewrite the claims file atomically (temp file + os.replace)."""
        tmp_file = self.claims_file.with_name(self.claims_file.name + ".tmp")
        try:
            async with aiofiles.open(tmp_file, "w", encoding="utf-8") as f:
                for item in self._claims_cache:
                    await f.write(json.dumps(item, ensure_ascii=False) + "\n")
            await asyncio.to_thread(os.replace, tmp_file, self.claims_file)
        except Exception as e:
            logger.error("claims_persist_failed", error=str(e), exc_info=True)
            with contextlib.suppress(OSError):
                tmp_file.unlink(missing_ok=True)

    async def add_claim(self, claim: dict[str, Any]) -> str:
        """Add a claim (append-only)."""
        if not self._loaded:
            await self.load()

        async with self._lock:
            claim_id = claim.get("id")
            if not claim_id:
                raise ValueError("Claim must have an 'id' field")

            self._claims_cache.append(claim)

            await self._ensure_dir()
            try:
                async with aiofiles.open(self.claims_file, "a", encoding="utf-8") as f:
                    await f.write(json.dumps(claim, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.error("claim_append_failed", error=str(e), exc_info=True)
                await self._persist()

            return cast(str, claim_id)

    async def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        """Get a claim by ID."""
        if not self._loaded:
            await self.load()

        async with self._lock:
            for item in self._claims_cache:
                if item.get("id") == claim_id:
                    return item
            return None

    async def get_recent_claims(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent active claims (superseded claims never surface)."""
        if not self._loaded:
            await self.load()

        async with self._lock:
            active = [c for c in self._claims_cache if c.get("status", "active") == "active"]
            return active[-limit:] if limit < len(active) else active.copy()

    async def get_all_claims(self, include_superseded: bool = False) -> list[dict[str, Any]]:
        """Get all claims."""
        if not self._loaded:
            await self.load()

        async with self._lock:
            if include_superseded:
                return self._claims_cache.copy()
            return [c for c in self._claims_cache if c.get("status", "active") == "active"]

    async def supersede_claim(self, claim_id: str, superseded_by: str, valid_to: str) -> bool:
        """Mark a claim superseded by a newer one.

        失效不删除 (invalidation without deletion, ADR 0017 stage 3): the
        claim stays in ``claims.jsonl`` for audit — only its
        ``status``/``superseded_by``/``valid_to`` fields change. The
        mutation rewrites the whole file atomically (temp file +
        os.replace), the same pattern as ``delete_claim``.

        Returns:
            True when the claim was found and marked; an unknown id is
            logged and returns False — never a silent no-op
        """
        if not self._loaded:
            await self.load()

        async with self._lock:
            for item in self._claims_cache:
                if item.get("id") == claim_id:
                    item["status"] = "superseded"
                    item["superseded_by"] = superseded_by
                    item["valid_to"] = valid_to
                    await self._persist()
                    return True
            logger.warning("claim_supersede_not_found", agent_id=self.agent_id, claim_id=claim_id)
            return False

    async def delete_claim(self, claim_id: str) -> bool:
        """Delete a claim by exact ID."""
        if not self._loaded:
            await self.load()

        async with self._lock:
            for i, item in enumerate(self._claims_cache):
                if item.get("id") == claim_id:
                    self._claims_cache.pop(i)
                    await self._persist()
                    return True
            return False

    async def search_claims_by_keyword(
        self, keywords: list[str], limit: int = 5
    ) -> list[dict[str, Any]]:
        """Search active claims by keywords (substring match on text)."""
        if not self._loaded:
            await self.load()

        async with self._lock:
            results = []
            for item in reversed(self._claims_cache):
                if item.get("status", "active") != "active":
                    continue
                text = str(item.get("text", ""))
                if any(kw.lower() in text.lower() for kw in keywords):
                    results.append(item)
                if len(results) >= limit:
                    break
            return results
