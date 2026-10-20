"""Claim store: atomic JSONL snapshots for memory claims."""

import asyncio
import contextlib
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import aiofiles

from tlon.base.logger import get_logger

logger = get_logger(__name__)


class ClaimStore:
    """Member-private claim snapshots with logical append semantics.

    Storage format: JSON Lines (``claims.jsonl``), one claim per line.
    Claims have no capacity cap — supersession marks, never deletes
    (same storage philosophy as the session journal). Deletion exists
    only for the agent-initiated ``forget``/reflection paths. Every mutation
    persists a private snapshot atomically before publishing it in memory
    (temp file + os.replace); failed writes never report successful receipt.

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
        """Publish a complete disk snapshot; I/O failures remain visible."""
        self._loaded = False
        await self._ensure_dir()
        claims: list[dict[str, Any]] = []
        if self.claims_file.exists():
            async with aiofiles.open(self.claims_file, encoding="utf-8") as stream:
                async for line in stream:
                    if not line.strip():
                        continue
                    try:
                        claims.append(json.loads(line))
                    except json.JSONDecodeError as error:
                        logger.warning("claim_line_parse_failed", error=str(error))
        self._claims_cache = claims
        self._loaded = True

    async def _persist(self, claims: list[dict[str, Any]]) -> None:
        """Persist a candidate snapshot before publishing it to readers."""
        await self._ensure_dir()
        temporary = self.claims_file.with_name(self.claims_file.name + ".tmp")
        try:
            async with aiofiles.open(temporary, "w", encoding="utf-8") as stream:
                for claim in claims:
                    await stream.write(json.dumps(claim, ensure_ascii=False) + "\n")
                await stream.flush()
                os.fsync(stream.fileno())
            # The short atomic commit is synchronous: cancellation cannot race
            # a background replace against reload or the next writer.
            os.replace(temporary, self.claims_file)
        finally:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)

    async def _commit(self, claims: list[dict[str, Any]]) -> None:
        try:
            await self._persist(claims)
        except BaseException:
            # Even a raised acknowledgement can follow a committed replace.
            # Reload disk facts; if reload fails, _loaded remains False.
            self._loaded = False
            await self.load()
            raise
        self._claims_cache = claims

    async def add_claim(self, claim: dict[str, Any]) -> str:
        """Append logically, with one atomic candidate snapshot commit."""
        async with self._lock:
            if not self._loaded:
                await self.load()
            claim_id = claim.get("id")
            if not claim_id:
                raise ValueError("Claim must have an 'id' field")
            candidate = deepcopy(self._claims_cache)
            candidate.append(deepcopy(claim))
            await self._commit(candidate)
            return cast(str, claim_id)

    async def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        """Get a claim by ID."""
        async with self._lock:
            if not self._loaded:
                await self.load()
            for item in self._claims_cache:
                if item.get("id") == claim_id:
                    return deepcopy(item)
            return None

    async def get_recent_claims(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent active claims (superseded claims never surface)."""
        async with self._lock:
            if not self._loaded:
                await self.load()
            active = [c for c in self._claims_cache if c.get("status", "active") == "active"]
            return deepcopy(active[-limit:] if limit < len(active) else active)

    async def get_all_claims(self, include_superseded: bool = False) -> list[dict[str, Any]]:
        """Get all claims."""
        async with self._lock:
            if not self._loaded:
                await self.load()
            if include_superseded:
                return deepcopy(self._claims_cache)
            return deepcopy(
                [c for c in self._claims_cache if c.get("status", "active") == "active"]
            )

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
        async with self._lock:
            if not self._loaded:
                await self.load()
            candidate = deepcopy(self._claims_cache)
            for item in candidate:
                if item.get("id") == claim_id:
                    item["status"] = "superseded"
                    item["superseded_by"] = superseded_by
                    item["valid_to"] = valid_to
                    await self._commit(candidate)
                    return True
            logger.warning("claim_supersede_not_found", agent_id=self.agent_id, claim_id=claim_id)
            return False

    async def delete_claim(self, claim_id: str) -> bool:
        """Delete a claim by exact ID."""
        async with self._lock:
            if not self._loaded:
                await self.load()
            for i, item in enumerate(self._claims_cache):
                if item.get("id") == claim_id:
                    candidate = deepcopy(self._claims_cache)
                    candidate.pop(i)
                    await self._commit(candidate)
                    return True
            return False

    async def search_claims_by_keyword(
        self, keywords: list[str], limit: int = 5
    ) -> list[dict[str, Any]]:
        """Search active claims by keywords (substring match on text)."""
        async with self._lock:
            if not self._loaded:
                await self.load()
            results = []
            for item in reversed(self._claims_cache):
                if item.get("status", "active") != "active":
                    continue
                text = str(item.get("text", ""))
                if any(kw.lower() in text.lower() for kw in keywords):
                    results.append(item)
                if len(results) >= limit:
                    break
            return deepcopy(results)
