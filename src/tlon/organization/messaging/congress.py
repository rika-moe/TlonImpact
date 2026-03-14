"""Congress for Tlon.

Manages chat room state, participants, and message history.
"""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import aiofiles

from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.organization.messaging.message import Message
from tlon.base.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Participant:
    """A chat room participant.
    
    Attributes:
        id: Unique participant ID
        name: Display name
        type: Participant type ("human" | "agent")
        metadata: Additional metadata
    """
    id: str
    name: str
    type: str  # "human" | "agent"
    metadata: Dict[str, any] = field(default_factory=dict)


@dataclass
class CongressMetadata:
    """Congress metadata.
    
    Attributes:
        id: Unique room ID
        name: Display name
        created_at: Creation timestamp
        data_dir: Directory for persistence
    """
    id: str
    name: str
    created_at: str
    data_dir: Path


class Congress:
    """Congress managing participants and message history.
    
    The Congress maintains the state of the conversation and persists
    messages to disk. It integrates with the EventBus for message
    distribution.
    
    Example:
        >>> bus = EventBus()
        >>> room = Congress(metadata, bus)
        >>> await room.initialize()
        >>> 
        >>> human = Participant(id="user1", name="User", type="human")
        >>> await room.join(human)
        >>> 
        >>> msg = Message.create(...)
        >>> await room.send_message(msg)
    """
    
    def __init__(self, metadata: CongressMetadata, event_bus: EventBus):
        """Initialize the chat room.
        
        Args:
            metadata: Room metadata
            event_bus: Event bus for message distribution
        """
        self.metadata = metadata
        self.event_bus = event_bus
        self.participants: Dict[str, Participant] = {}
        self.history: List[Message] = []
        self._lock = asyncio.Lock()
        self._mention_subscription: Optional[str] = None
    
    async def initialize(self) -> None:
        """Initialize the chat room.
        
        Loads history from disk and sets up event subscriptions.
        """
        await self._load_history()
        logger.info(f"Congress '{self.metadata.name}' initialized with {len(self.history)} messages")
    
    async def shutdown(self) -> None:
        """Shutdown the chat room.
        
        Ensures all pending writes are completed.
        """
        # Save current state
        await self._save_all_history()
        logger.info(f"Congress '{self.metadata.name}' shutdown")
    
    async def join(self, participant: Participant) -> None:
        """Add a participant to the room.
        
        Args:
            participant: The participant to add
        """
        async with self._lock:
            self.participants[participant.id] = participant
        
        # Broadcast system message
        system_msg = Message.create(
            sender_id="system",
            sender_name="系统",
            sender_type="system",
            content=f"{participant.name} 加入了聊天室",
            content_type="system",
        )
        await self.send_message(system_msg)
        
        logger.info(f"Participant joined: {participant.name} ({participant.id})")
    
    async def leave(self, participant_id: str) -> None:
        """Remove a participant from the room.
        
        Args:
            participant_id: ID of the participant to remove
        """
        async with self._lock:
            participant = self.participants.pop(participant_id, None)
        
        if participant:
            system_msg = Message.create(
                sender_id="system",
                sender_name="系统",
                sender_type="system",
                content=f"{participant.name} 离开了聊天室",
                content_type="system",
            )
            await self.send_message(system_msg)
            logger.info(f"Participant left: {participant.name} ({participant_id})")
    
    async def send_message(self, message: Message) -> None:
        """Send a message to the room.
        
        The message is added to history, persisted, and published
        to the event bus.
        
        Args:
            message: The message to send
        """
        logger.info(f"[Congress] Sending message from {message.sender_name} ({message.sender_id}): {message.content[:50]}...")
        
        async with self._lock:
            self.history.append(message)
            # Persist to disk
            await self._save_message(message)
        
        # Publish to event bus
        event = Event(
            type=EventType.MESSAGE_NEW,
            payload=message,
            source=message.sender_id,
        )
        logger.debug(f"[Congress] Publishing MESSAGE_NEW event to EventBus (event_bus running: {self.event_bus.is_running()})")
        await self.event_bus.publish(event)
        logger.debug(f"[Congress] Event published successfully")
        
        # Check for mentions and publish mention event
        if message.mentions:
            mention_event = Event(
                type=EventType.MESSAGE_MENTION,
                payload=message,
                source=message.sender_id,
            )
            await self.event_bus.publish(mention_event)
            logger.debug(f"Mention event published for {len(message.mentions)} users")
    
    async def get_history(
        self, 
        limit: int = 100, 
        before_id: Optional[str] = None
    ) -> List[Message]:
        """Get message history.
        
        Args:
            limit: Maximum number of messages to return
            before_id: If specified, return messages before this ID
            
        Returns:
            List of messages (newest first)
        """
        async with self._lock:
            if before_id:
                # Find position of before_id
                for idx, msg in enumerate(self.history):
                    if msg.id == before_id:
                        start = max(0, idx - limit)
                        return self.history[start:idx]
                return []
            else:
                # Return last 'limit' messages
                return self.history[-limit:] if limit < len(self.history) else self.history.copy()
    
    async def get_participant(self, participant_id: str) -> Optional[Participant]:
        """Get a participant by ID.
        
        Args:
            participant_id: The participant ID
            
        Returns:
            The participant or None if not found
        """
        return self.participants.get(participant_id)
    
    def get_all_participants(self) -> List[Participant]:
        """Get all participants.
        
        Returns:
            List of all participants
        """
        return list(self.participants.values())
    
    def get_participant_names(self) -> List[str]:
        """Get names of all participants.
        
        Returns:
            List of participant names
        """
        return [p.name for p in self.participants.values()]
    
    async def _load_history(self) -> None:
        """Load message history from disk."""
        history_file = self.metadata.data_dir / "chat_history.jsonl"
        
        if not history_file.exists():
            logger.info(f"No history file found at {history_file}")
            return
        
        try:
            async with aiofiles.open(history_file, 'r', encoding='utf-8') as f:
                async for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        self.history.append(Message.from_dict(data))
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse history line: {e}")
                    except Exception as e:
                        logger.error(f"Error loading history entry: {e}")
            
            logger.info(f"Loaded {len(self.history)} messages from history")
        except Exception as e:
            logger.error(f"Error loading history: {e}", exc_info=True)
    
    async def _save_message(self, message: Message) -> None:
        """Save a single message to disk.
        
        Args:
            message: The message to save
        """
        self.metadata.data_dir.mkdir(parents=True, exist_ok=True)
        history_file = self.metadata.data_dir / "chat_history.jsonl"
        
        try:
            async with aiofiles.open(history_file, 'a', encoding='utf-8') as f:
                await f.write(json.dumps(message.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Error saving message: {e}", exc_info=True)
    
    async def _save_all_history(self) -> None:
        """Save all history to disk (for backup/compact)."""
        self.metadata.data_dir.mkdir(parents=True, exist_ok=True)
        history_file = self.metadata.data_dir / "chat_history.jsonl"
        
        try:
            async with aiofiles.open(history_file, 'w', encoding='utf-8') as f:
                for msg in self.history:
                    await f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")
            
            logger.info(f"Saved {len(self.history)} messages to history")
        except Exception as e:
            logger.error(f"Error saving history: {e}", exc_info=True)
