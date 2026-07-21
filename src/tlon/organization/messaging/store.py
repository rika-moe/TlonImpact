"""Message Store abstraction for Tlon.

Provides the storage interface for message history with query capabilities.
This is the "stateful" part of the messaging system, separated from
the stateless MessageRouter.
"""

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import aiofiles

from tlon.base.logger import get_logger
from tlon.organization.messaging.message import Message

logger = get_logger(__name__)


@dataclass
class MessageFilter:
    """Filter criteria for message queries.

    Attributes:
        sender_type: Filter by sender type ("human", "agent", "system")
        sender_id: Filter by specific sender ID
        before: Messages before this timestamp
        after: Messages after this timestamp
        content_contains: Content substring search
        mentions: Must mention these IDs
    """

    sender_type: str | None = None
    sender_id: str | None = None
    before: datetime | None = None
    after: datetime | None = None
    content_contains: str | None = None
    mentions: list[str] = field(default_factory=list)


class MessageStore(ABC):
    """Abstract base class for message storage.

    MessageStore is responsible for:
    - Persisting messages durably
    - Providing query capabilities for historical messages
    - Serving as the "source of truth" for message history

    Implementations can use different backends (file, database, etc.)

    Example:
        >>> store = FileMessageStore(data_dir=Path("./data"))
        >>> await store.append(message)
        >>> history = await store.get_history(limit=50)
    """

    @abstractmethod
    async def append(self, message: Message) -> None:
        """Append a message to the store.

        This method should be atomic and durable.

        Args:
            message: The message to store
        """
        pass

    @abstractmethod
    async def get_history(
        self, limit: int = 100, before_id: str | None = None, filter: MessageFilter | None = None
    ) -> list[Message]:
        """Get message history with optional filtering.

        Args:
            limit: Maximum number of messages to return
            before_id: Return messages before this message ID (for pagination)
            filter: Optional filter criteria

        Returns:
            List of messages in chronological order (oldest first)
        """
        pass

    @abstractmethod
    async def get_by_id(self, message_id: str) -> Message | None:
        """Get a specific message by ID.

        Args:
            message_id: The message ID to look up

        Returns:
            The message if found, None otherwise
        """
        pass

    @abstractmethod
    async def get_context_window(
        self, around_id: str, before: int = 10, after: int = 0
    ) -> list[Message]:
        """Get messages around a specific message (for context building).

        Args:
            around_id: The message ID to center around
            before: Number of messages before the target
            after: Number of messages after the target

        Returns:
            List of messages in chronological order
        """
        pass


class FileMessageStore(MessageStore):
    """File-based message store using append-only JSONL.

    Stores messages in a JSONL file for durability.
    Keeps recent messages in memory for fast queries.

    Thread-safe using asyncio locks.
    """

    def __init__(self, data_dir: Path, filename: str = "messages.jsonl"):
        """Initialize the file-based store.

        Args:
            data_dir: Directory for persistence
            filename: Name of the storage file
        """
        self._data_dir = data_dir
        self._filename = filename
        self._file_path = data_dir / filename

        # In-memory cache for recent messages (circular buffer concept)
        self._cache: list[Message] = []
        self._cache_size = 1000  # Keep last 1000 messages in memory

        # Lock for thread safety
        self._lock = asyncio.Lock()

        logger.info("message_store_initialized", file_path=str(self._file_path))

    async def initialize(self) -> None:
        """Initialize by loading existing messages into cache."""
        self._data_dir.mkdir(parents=True, exist_ok=True)

        if not self._file_path.exists():
            logger.info("message_store_fresh_start")
            return

        try:
            async with aiofiles.open(self._file_path, encoding="utf-8") as f:
                content = await f.read()

            lines = content.strip().split("\n")
            messages = []

            for line_num, line in enumerate(lines):
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    msg = Message.from_dict(data)
                    messages.append(msg)
                except Exception as e:
                    logger.warning(
                        "message_parse_failed", line_number=line_num, error=str(e)
                    )

            # Keep only recent messages in cache
            self._cache = messages[-self._cache_size :]

            logger.info(
                "message_history_loaded",
                loaded_count=len(messages),
                cached_count=len(self._cache),
            )

        except Exception as e:
            logger.error("message_history_load_failed", error=str(e), exc_info=True)

    async def append(self, message: Message) -> None:
        """Append message to file and cache.

        Args:
            message: The message to store
        """
        async with self._lock:
            # Add to cache
            self._cache.append(message)
            if len(self._cache) > self._cache_size:
                self._cache.pop(0)  # Remove oldest

            # Persist to file
            await self._persist(message)

    async def _persist(self, message: Message) -> None:
        """Persist a single message to disk."""
        try:
            async with aiofiles.open(self._file_path, "a", encoding="utf-8") as f:
                await f.write(
                    json.dumps(message.to_dict(), ensure_ascii=False) + "\n"
                )
        except Exception as e:
            logger.error(
                "message_persist_failed",
                message_id=message.id,
                error=str(e),
                exc_info=True,
            )
            raise

    async def get_history(
        self, limit: int = 100, before_id: str | None = None, filter: MessageFilter | None = None
    ) -> list[Message]:
        """Get message history from cache or file.

        Implementation prioritizes cache for recent messages.
        """
        async with self._lock:
            # Start with cache (which contains most recent)
            messages = list(self._cache)

            # If we need more than cache has, read from file
            if len(messages) < limit and before_id is None:
                messages = await self._read_from_file(limit)

            # Apply before_id filter
            if before_id:
                cutoff_idx = None
                for idx, msg in enumerate(messages):
                    if msg.id == before_id:
                        cutoff_idx = idx
                        break
                if cutoff_idx is not None:
                    messages = messages[:cutoff_idx]

            # Apply filter
            if filter:
                messages = self._apply_filter(messages, filter)

            # Return last 'limit' messages
            return messages[-limit:] if len(messages) > limit else messages

    async def _read_from_file(self, limit: int) -> list[Message]:
        """Read messages directly from file.

        This is a fallback when cache doesn't have enough messages.
        """
        if not self._file_path.exists():
            return []

        try:
            async with aiofiles.open(self._file_path, encoding="utf-8") as f:
                lines = await f.readlines()

            messages = []
            for line in lines:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line.strip())
                    messages.append(Message.from_dict(data))
                except Exception:
                    continue

            return messages

        except Exception as e:
            logger.error("message_file_read_failed", error=str(e))
            return []

    def _apply_filter(self, messages: list[Message], filter: MessageFilter) -> list[Message]:
        """Apply filter criteria to message list."""
        result = messages

        if filter.sender_type:
            result = [m for m in result if m.sender_type == filter.sender_type]

        if filter.sender_id:
            result = [m for m in result if m.sender_id == filter.sender_id]

        if filter.before:
            result = [m for m in result if m.timestamp < filter.before]

        if filter.after:
            result = [m for m in result if m.timestamp > filter.after]

        if filter.content_contains:
            result = [m for m in result if filter.content_contains.lower() in m.content.lower()]

        if filter.mentions:
            result = [
                m for m in result if any(mention in m.mentions for mention in filter.mentions)
            ]

        return result

    async def get_by_id(self, message_id: str) -> Message | None:
        """Get message by ID via linear scan (cache first, then file)."""
        async with self._lock:
            # Check cache first
            for msg in reversed(self._cache):
                if msg.id == message_id:
                    return msg

            # Fall back to a linear scan of the file if not in cache
            all_messages = await self._read_from_file(10000)
            for msg in all_messages:
                if msg.id == message_id:
                    return msg

            return None

    async def get_context_window(
        self, around_id: str, before: int = 10, after: int = 0
    ) -> list[Message]:
        """Get context window around a message."""
        async with self._lock:
            # Read all messages (for small datasets this is fine)
            # For large datasets, we'd need a more sophisticated index
            all_messages = await self._read_from_file(10000)

            # Find target position
            target_idx = None
            for idx, msg in enumerate(all_messages):
                if msg.id == around_id:
                    target_idx = idx
                    break

            if target_idx is None:
                return []

            # Calculate window
            start_idx = max(0, target_idx - before)
            end_idx = min(len(all_messages), target_idx + after + 1)

            return all_messages[start_idx:end_idx]
