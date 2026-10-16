"""Event system protocol.

This module defines the Event types used across all Tlon components.
It is designed to be serialization-friendly for wire transmission (SSE).
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any


class EventType(StrEnum):
    """Types of events in the system.

    Using str Enum for easy JSON serialization/deserialization.
    """

    # Mail events (Republic of Letters, see docs/design/mail.md)
    MAIL_NEW = "mail.new"  # A mail was committed by the post office (notification only)
    MAIL_DELIVERED = "mail.delivered"  # A mail landed in one recipient's inbox
    MAIL_ENDORSED = "mail.endorsed"  # A delivered mail endorses another mail (reputation signal, see docs/design/mail.md)

    # Agent events
    AGENT_JOIN = "agent.join"
    AGENT_LEAVE = "agent.leave"
    AGENT_THINKING = "agent.thinking"
    AGENT_RESPONSE = "agent.response"
    AGENT_STARTED = "agent.started"
    AGENT_STOPPED = "agent.stopped"
    AGENT_RETIRED = "agent.retired"  # Retirement ceremony fact: handover inventory + farewell outcome (see docs/design/mail.md 「退休交接信」)
    AGENT_ERROR = "agent.error"
    AGENT_STATE_CHANGED = "agent.state_changed"
    # Dynamic registry (wake/sleep) lifecycle events
    AGENT_WAKING = "agent.waking"
    AGENT_WOKEN = "agent.woken"
    AGENT_WAKE_FAILED = "agent.wake_failed"
    AGENT_SLEEPING = "agent.sleeping"
    # Policy/mechanism split (members.md 阶段 D): Adam decides, the registry
    # executes. AGENT_IDLE is the registry's idle-fact report; the requests
    # are Adam's decisions carried on the bus.
    AGENT_START_REQUEST = "agent.start_request"
    AGENT_STOP_REQUEST = "agent.stop_request"
    AGENT_IDLE = "agent.idle"
    # Dynamic registry (spawn) lifecycle events
    AGENT_SPAWNED = "agent.spawned"

    # Public health facts (ADR 0003): detection only, never enforcement —
    # the monitor publishes facts; acting on them is policy (Adam/operator)
    AGENT_NOISY = (
        "agent.noisy"  # A member is generating social noise (mail burst / broadcast storm)
    )
    MAIL_ORPHAN_SCAN = (
        "mail.orphan_scan"  # Periodic orphan-mail (unanswered thread root) scan statistics
    )

    # Agent thinking process (observability)
    AGENT_THINKING_START = "agent.thinking_start"
    AGENT_THINKING_END = "agent.thinking_end"
    AGENT_LLM_REQUEST = "agent.llm_request"
    AGENT_LLM_RESPONSE = "agent.llm_response"

    # Tool events
    TOOL_EXECUTE = "tool.execute"
    TOOL_RESULT = "tool.result"
    TOOL_ERROR = "tool.error"

    # Tool execution (observability)
    TOOL_EXECUTE_START = "tool.execute_start"
    TOOL_EXECUTE_END = "tool.execute_end"

    # System events
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"
    SYSTEM_ERROR = "system.error"

    # Config events
    CONFIG_UPDATED = "config.updated"


@dataclass
class Event:
    """Event for transmission via Event Bus.

    This class is designed to be shared across all components:
    - Server (EventBus, MailDelivery)
    - Agents
    - TUI/Web clients

    Attributes:
        type: The type of event (EventType enum or string)
        payload: Event data (arbitrary, but should be JSON-serializable)
        source: ID of the event source (agent_id, "human", "system")
        timestamp: When the event was created (defaults to now)
        id: Unique event identifier (auto-generated UUID)
        trace_id: Trace ID for distributed tracing (UUID v4)
        span_id: Span ID for the current operation (UUID v4)
        parent_span_id: Parent span ID for hierarchical tracing (optional)
    """

    type: EventType
    payload: Any
    source: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize event to dictionary for JSON transmission.

        Returns:
            Dictionary representation of the event
        """
        payload = self.payload
        # Special handling for objects with to_dict method (like Message)
        # Using duck typing to avoid circular imports
        if payload is not None and hasattr(payload, "to_dict") and callable(payload.to_dict):
            payload = payload.to_dict()

        result = {
            "id": self.id,
            "type": self.type.value if isinstance(self.type, Enum) else str(self.type),
            "payload": payload,
            "source": self.source,
            "timestamp": self.timestamp.isoformat(),
        }

        # Add tracing fields if present
        if self.trace_id:
            result["trace_id"] = self.trace_id
        if self.span_id:
            result["span_id"] = self.span_id
        if self.parent_span_id:
            result["parent_span_id"] = self.parent_span_id

        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
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
                data["timestamp"] = datetime.now(UTC)
        elif isinstance(ts, (int, float)):
            # Unix timestamp (seconds since epoch)
            data["timestamp"] = datetime.fromtimestamp(ts, UTC)
        elif not isinstance(ts, datetime):
            # Unknown format, default to now
            data["timestamp"] = datetime.now(UTC)

        # Ensure tracing fields are present
        if "trace_id" not in data:
            data["trace_id"] = None
        if "span_id" not in data:
            data["span_id"] = None
        if "parent_span_id" not in data:
            data["parent_span_id"] = None

        return cls(**data)


# Type aliases for convenience
EventHandler = Any  # Callable[[Event], None] - avoid import issues
EventSubscription = str
