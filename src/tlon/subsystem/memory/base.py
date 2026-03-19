"""Base class for Memory stores."""

from abc import ABC, abstractmethod
from typing import Any


class MemoryStore(ABC):
    """Abstract base class for memory storage backends.

    Provides a common interface for different storage implementations
    (JSONL files, SQLite, vector DB, etc.)

    Usage:
        >>> class MyStore(MemoryStore):
        ...     async def add(self, item: Dict) -> str:
        ...         # Implementation
        ...         pass
    """

    def __init__(self, agent_id: str):
        """Initialize store.

        Args:
            agent_id: The agent this store belongs to
        """
        self.agent_id = agent_id

    @abstractmethod
    async def add(self, item: dict[str, Any]) -> str:
        """Add an item to storage.

        Args:
            item: The item to store

        Returns:
            The ID of the stored item
        """
        pass

    @abstractmethod
    async def get(self, item_id: str) -> dict[str, Any] | None:
        """Get an item by ID.

        Args:
            item_id: The item ID

        Returns:
            The item if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get the most recent N items.

        Args:
            limit: Maximum number of items to return

        Returns:
            List of items, ordered by recency (newest first)
        """
        pass

    @abstractmethod
    async def delete(self, item_id: str) -> bool:
        """Delete an item by ID.

        Args:
            item_id: The item ID to delete

        Returns:
            True if deleted, False if not found
        """
        pass

    async def search_by_keyword(self, keywords: list[str], limit: int = 5) -> list[dict[str, Any]]:
        """Search items by keywords.

        Simple implementation: matches against keywords field.
        Override for more sophisticated search.

        Args:
            keywords: List of keywords to search for
            limit: Maximum results to return

        Returns:
            Matching items
        """
        # Default implementation: get all and filter
        all_items = await self.get_recent(limit=1000)
        results = []

        for item in all_items:
            item_keywords = item.get("keywords", [])
            if any(kw.lower() in [ik.lower() for ik in item_keywords] for kw in keywords):
                results.append(item)
                if len(results) >= limit:
                    break

        return results

    @abstractmethod
    async def clear(self) -> None:
        """Clear all items from storage."""
        pass

    @abstractmethod
    async def count(self) -> int:
        """Get total count of items in storage.

        Returns:
            Number of items
        """
        pass
