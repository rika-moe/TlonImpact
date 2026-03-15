"""Event system protocol.

This module defines the Event types used across all Tlon components.
It is designed to be serialization-friendly for WebSocket transmission.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional


class EventType(str, Enum):
    """Types of events in the system.
    
    Using str Enum for easy JSON serialization/deserialization.
    """
    # Message events
    MESSAGE_NEW = "message.new"

    # Agent events
    AGENT_JOIN = "agent.join"
    AGENT_LEAVE = "agent.leave"
    AGENT_THINKING = "agent.thinking"
    AGENT_RESPONSE = "agent.response"
    AGENT_STARTED = "agent.started"
    AGENT_STOPPED = "agent.stopped"
    AGENT_ERROR = "agent.error"
    AGENT_STATE_CHANGED = "agent.state_changed"
    
    # Tool events
    TOOL_EXECUTE = "tool.execute"
    TOOL_RESULT = "tool.result"
    TOOL_ERROR = "tool.error"
    
    # System events
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"
    SYSTEM_ERROR = "system.error"


@dataclass
class Event:
    """Event for transmission via Event Bus.
    
    This class is designed to be shared across all components:
    - Server (EventBus, ChatRoom)
    - Agents
    - TUI/Web clients
    
    Attributes:
        type: The type of event (EventType enum or string)
        payload: Event data (arbitrary, but should be JSON-serializable)
        source: ID of the event source (agent_id, "human", "system")
        timestamp: When the event was created (defaults to now)
        id: Unique event identifier (auto-generated UUID)
    """
    type: EventType
    payload: Any
    source: str
    timestamp: datetime = field(default_factory=datetime.now)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize event to dictionary for JSON transmission.
        
        Returns:
            Dictionary representation of the event
        """
        payload = self.payload
        # Special handling for objects with to_dict method (like Message)
        # Using duck typing to avoid circular imports
        if payload is not None and hasattr(payload, 'to_dict') and callable(payload.to_dict):
            payload = payload.to_dict()
        
        return {
            "id": self.id,
            "type": self.type.value if isinstance(self.type, Enum) else str(self.type),
            "payload": payload,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        """Deserialize event from dictionary.
        
        Handles multiple timestamp formats for compatibility:
        - ISO format string (from server)
        - Unix timestamp float/int (from some clients)
        - datetime object (already parsed)
        
        Args:
            data: Dictionary containing event data
            
        Returns:
            An Event instance
        """
        data = data.copy()
        
        # Convert type string to Enum
        type_value = data.get("type")
        if isinstance(type_value, str):
            try:
                data["type"] = EventType(type_value)
            except ValueError:
                # Keep as string if not a known type
                data["type"] = type_value
        
        # Convert timestamp - handle multiple formats
        ts = data.get("timestamp")
        if isinstance(ts, str):
            try:
                data["timestamp"] = datetime.fromisoformat(ts)
            except ValueError:
                # Fallback to now if invalid
                data["timestamp"] = datetime.now()
        elif isinstance(ts, (int, float)):
            # Unix timestamp (seconds since epoch)
            data["timestamp"] = datetime.fromtimestamp(ts)
        elif not isinstance(ts, datetime):
            # Unknown format, default to now
            data["timestamp"] = datetime.now()
        
        return cls(**data)


# Type aliases for convenience
EventHandler = Any  # Callable[[Event], None] - avoid import issues
EventSubscription = str
