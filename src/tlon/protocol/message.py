"""Message protocol for Tlon.

This module defines the unified message format used throughout the system.
It supports serialization for:
1. Display in user interfaces
2. Transmission via Event Bus
3. Conversion to OpenAI API format
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class MessageType(Enum):
    """Types of messages."""
    TEXT = "text"
    CODE = "code"
    IMAGE = "image"
    FILE = "file"
    SYSTEM = "system"


@dataclass
class Message:
    """Unified message format for internal use.
    
    Attributes:
        id: Unique message identifier
        sender_id: ID of the sender (human or agent)
        sender_name: Display name of the sender
        sender_type: Type of sender ("human", "agent", "system")
        content: Message content (text)
        content_type: Type of content (text, code, image, file, system)
        mentions: List of user IDs mentioned in the message
        reply_to: ID of the message being replied to (optional)
        timestamp: When the message was sent
        metadata: Additional metadata (arbitrary dict)
    """
    id: str
    sender_id: str
    sender_name: str
    sender_type: str  # "human" | "agent" | "system"
    content: str
    content_type: str = "text"
    mentions: List[str] = field(default_factory=list)
    reply_to: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Validate sender_type."""
        if self.sender_type not in ("human", "agent", "system"):
            raise ValueError(f"Invalid sender_type: {self.sender_type}")
    
    @classmethod
    def create(
        cls,
        sender_id: str,
        sender_name: str,
        sender_type: str,
        content: str,
        content_type: str = "text",
        mentions: Optional[List[str]] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Message":
        """Factory method to create a new message with auto-generated ID.
        
        Args:
            sender_id: ID of the sender
            sender_name: Display name of the sender
            sender_type: Type of sender ("human", "agent", "system")
            content: Message content
            content_type: Type of content
            mentions: List of mentioned user IDs
            reply_to: ID of message being replied to
            metadata: Additional metadata
            
        Returns:
            A new Message instance
        """
        return cls(
            id=str(uuid.uuid4()),
            sender_id=sender_id,
            sender_name=sender_name,
            sender_type=sender_type,
            content=content,
            content_type=content_type,
            mentions=mentions or [],
            reply_to=reply_to,
            metadata=metadata or {},
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize message to dictionary.
        
        Returns:
            Dictionary representation of the message
        """
        return {
            "id": self.id,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "sender_type": self.sender_type,
            "content": self.content,
            "content_type": self.content_type,
            "mentions": self.mentions,
            "reply_to": self.reply_to,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """Deserialize message from dictionary.
        
        Args:
            data: Dictionary containing message data
            
        Returns:
            A Message instance
        """
        data = data.copy()
        if "timestamp" in data and isinstance(data["timestamp"], str):
            data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        return cls(**data)
    
    def to_openai_message(self) -> Dict[str, Any]:
        """Convert message to OpenAI API format.
        
        Returns:
            Dictionary in OpenAI message format
        """
        role = "assistant" if self.sender_type == "agent" else "user"
        
        # System messages from "system" sender type
        if self.sender_type == "system":
            role = "system"
        
        message = {
            "role": role,
            "content": self.content,
        }
        
        # Add name for non-agent users (helps distinguish participants)
        if self.sender_type != "agent" and self.sender_type != "system":
            # OpenAI only allows alphanumeric and underscores in name
            safe_name = "".join(c if c.isalnum() or c == "_" else "_" for c in self.sender_id)
            message["name"] = safe_name[:64]  # OpenAI limit
        
        return message
    
    def format_for_display(self, format_type: str = "text") -> str:
        """Format message for display.
        
        Args:
            format_type: Output format ("text" or "markdown")
            
        Returns:
            Formatted string
        """
        time_str = self.timestamp.strftime("%H:%M")
        
        if format_type == "markdown":
            mention_str = ""
            if self.mentions:
                mention_str = " " + " ".join([f"`@{m}`" for m in self.mentions])
            
            if self.content_type == "code":
                return f"**[{self.sender_name}]** {time_str}:\n```\n{self.content}\n```{mention_str}"
            else:
                return f"**[{self.sender_name}]** {time_str}: {self.content}{mention_str}"
        else:
            mention_str = ""
            if self.mentions:
                mention_str = " " + " ".join([f"@{m}" for m in self.mentions])
            return f"[{self.sender_name}] {time_str}: {self.content}{mention_str}"
    
    def is_mentioning(self, user_id: str) -> bool:
        """Check if this message mentions a specific user.
        
        Args:
            user_id: User ID to check
            
        Returns:
            True if the user is mentioned
        """
        return user_id in self.mentions
    
    def extract_mentions_from_content(self) -> List[str]:
        """Extract @mentions from message content.
        
        Returns:
            List of mentioned IDs/names found in content
        """
        import re
        # Pattern to match @username or @"User Name"
        pattern = r'@"([^"]+)"|@(\S+)'
        matches = re.findall(pattern, self.content)
        # Flatten the tuple results
        mentions = []
        for match in matches:
            mentions.extend([m for m in match if m])
        return mentions


# Type alias for message list
MessageList = List[Message]
