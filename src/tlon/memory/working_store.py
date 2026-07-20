"""Working Memory store implementation using JSONL files."""

import asyncio
import json
from pathlib import Path
from typing import Any

import aiofiles

from tlon.base.logger import get_logger
from tlon.base.paths import paths

from .base import MemoryStore

logger = get_logger(__name__)


class _ToolCallEncoder(json.JSONEncoder):
    """Custom JSON encoder to handle ToolCall objects."""

    def default(self, obj: Any) -> Any:
        # Handle ToolCall objects
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        # Handle other non-serializable types
        if isinstance(obj, set):
            return list(obj)
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        # Let the base class raise TypeError
        return super().default(obj)


class WorkingMemoryStore(MemoryStore):
    """Working Memory store for recent interactions.

    Stores raw interactions (user message + agent response + tool results)
    in a rolling window. When the window is full, interactions are
    summarized and moved to Episodic Memory.

    Storage format: JSON Lines (JSONL), one interaction per line

    Attributes:
        agent_id: The agent this store belongs to
        storage_path: Path to the storage directory
        max_items: Maximum number of interactions to keep

    Example:
        >>> store = WorkingMemoryStore("dev-001", Path("./data/agents/dev-001/memory"))
        >>> await store.load()
        >>> interaction_id = await store.add(interaction_dict)
        >>> recent = await store.get_recent(limit=5)
    """

    def __init__(self, agent_id: str, storage_path: Path, max_items: int = 10):
        """Initialize Working Memory store.

        Args:
            agent_id: The agent ID
            storage_path: Ignored (for backward compat). Uses TlonPaths internally.
            max_items: Maximum interactions to keep (default: 10)
        """
        super().__init__(agent_id)
        self.working_dir = paths.memory_working_dir(agent_id)
        self.interactions_file = self.working_dir / "interactions.jsonl"
        self.max_items = max_items
        self._cache: list[dict[str, Any]] = []
        self._loaded = False
        self._lock = asyncio.Lock()

    async def _ensure_dir(self) -> None:
        """Ensure storage directory exists."""
        self.working_dir.mkdir(parents=True, exist_ok=True)

    async def load(self) -> None:
        """Load interactions from disk into cache.

        Should be called once at startup.
        """
        await self._ensure_dir()

        if not self.interactions_file.exists():
            logger.info(f"No existing working memory for {self.agent_id}")
            self._cache = []
            self._loaded = True
            return

        try:
            self._cache = []
            async with aiofiles.open(self.interactions_file, encoding="utf-8") as f:
                async for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        self._cache.append(data)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse interaction line: {e}")

            # Trim if exceeds max
            if len(self._cache) > self.max_items:
                self._cache = self._cache[-self.max_items :]
                await self._persist()

            self._loaded = True
            logger.info(f"Loaded {len(self._cache)} interactions for {self.agent_id}")

        except Exception as e:
            logger.error(f"Error loading working memory: {e}")
            self._cache = []
            self._loaded = True

    async def _persist(self) -> None:
        """Write cache to disk."""
        await self._ensure_dir()

        try:
            async with aiofiles.open(self.interactions_file, "w", encoding="utf-8") as f:
                for item in self._cache:
                    await f.write(json.dumps(item, ensure_ascii=False, cls=_ToolCallEncoder) + "\n")
        except Exception as e:
            logger.error(f"Error persisting working memory: {e}")

    async def add(self, item: dict[str, Any]) -> str:
        """Add an interaction.

        Args:
            item: The interaction dictionary (must have 'id' field)

        Returns:
            The interaction ID
        """
        if not self._loaded:
            await self.load()

        async with self._lock:
            return await self._add_locked(item)

    async def _add_locked(self, item: dict[str, Any]) -> str:
        item_id = item.get("id")
        if not item_id:
            raise ValueError("Interaction must have an 'id' field")

        self._cache.append(item)

        if len(self._cache) > self.max_items:
            removed = self._cache[: -self.max_items]
            self._cache = self._cache[-self.max_items :]
            logger.debug(f"Trimmed {len(removed)} old interactions from working memory")

        await self._ensure_dir()
        try:
            async with aiofiles.open(self.interactions_file, "a", encoding="utf-8") as f:
                await f.write(json.dumps(item, ensure_ascii=False, cls=_ToolCallEncoder) + "\n")
        except Exception as e:
            logger.error(f"Error appending interaction: {e}")
            await self._persist()

        return item_id

    async def get(self, item_id: str) -> dict[str, Any] | None:
        """Get an interaction by ID."""
        if not self._loaded:
            await self.load()

        async with self._lock:
            for item in self._cache:
                if item.get("id") == item_id:
                    return item
            return None

    async def get_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent interactions in chronological order (oldest first, newest last)."""
        if not self._loaded:
            await self.load()

        async with self._lock:
            return self._cache[-limit:] if limit < len(self._cache) else self._cache.copy()

    async def get_all(self) -> list[dict[str, Any]]:
        """Get all interactions in working memory."""
        if not self._loaded:
            await self.load()

        async with self._lock:
            return self._cache.copy()

    async def delete(self, item_id: str) -> bool:
        """Delete an interaction by ID."""
        if not self._loaded:
            await self.load()

        async with self._lock:
            for i, item in enumerate(self._cache):
                if item.get("id") == item_id:
                    self._cache.pop(i)
                    await self._persist()
                    return True
            return False

    async def clear(self) -> None:
        """Clear all interactions."""
        async with self._lock:
            self._cache = []
            if self.interactions_file.exists():
                try:
                    self.interactions_file.unlink()
                except Exception as e:
                    logger.error(f"Error clearing working memory file: {e}")

    async def count(self) -> int:
        """Get count of interactions."""
        if not self._loaded:
            await self.load()
        async with self._lock:
            return len(self._cache)

    async def is_full(self) -> bool:
        """Check if working memory is at capacity."""
        count = await self.count()
        return count >= self.max_items

    async def get_oldest(self, n: int = 1) -> list[dict[str, Any]]:
        """Get the oldest N interactions."""
        if not self._loaded:
            await self.load()
        async with self._lock:
            return self._cache[:n] if n <= len(self._cache) else self._cache.copy()

    async def remove_by_ids(self, ids: set[str]) -> int:
        """Remove interactions by ID set atomically. Returns count removed."""
        async with self._lock:
            before = len(self._cache)
            self._cache = [i for i in self._cache if i.get("id") not in ids]
            removed = before - len(self._cache)
            if removed:
                await self._persist()
            return removed

    async def archive_and_clear(self) -> list[dict[str, Any]]:
        """Archive all interactions and clear working memory."""
        if not self._loaded:
            await self.load()

        async with self._lock:
            archived = self._cache.copy()
            await self._clear_locked()
            logger.info(f"Archived {len(archived)} interactions from working memory")
            return archived

    async def _clear_locked(self) -> None:
        """Clear without acquiring lock (caller holds lock)."""
        self._cache = []
        if self.interactions_file.exists():
            try:
                self.interactions_file.unlink()
            except Exception as e:
                logger.error(f"Error clearing working memory file: {e}")
