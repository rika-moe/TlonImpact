"""Client Adapter Pattern for Tlon SDK.

Adapters encapsulate client-type-specific behavior:
- TerminalAdapter: Receives all events, supports event filtering by trace_id
- WebAdapter: Receives filtered events (excludes high-frequency events)

Each adapter provides:
- Client type identification
- Event filtering logic
- Protocol capabilities
- Client-specific metadata
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set
from enum import Enum


class ClientType(str, Enum):
    """Supported client types."""
    TUI = "tui"
    WEB = "web"
    TELEGRAM = "telegram"


@dataclass
class ProtocolCapabilities:
    """Protocol capabilities supported by the client."""
    trace: bool = True
    filter: bool = True
    history: bool = True
    # TODO: Add more capabilities as needed


class ClientAdapter(ABC):
    """Abstract base class for client adapters.

    Adapters encapsulate client-type-specific behavior including:
    - Client type identification
    - Event filtering logic
    - Protocol capabilities
    - Client-specific metadata
    """

    def __init__(self, client_type: ClientType):
        self._client_type = client_type
        self._capabilities = self._get_capabilities()
        self._event_filters: Optional[Dict[str, Any]] = None
        self._trace_filter: Optional[str] = None

    @property
    def client_type(self) -> str:
        """Return the client type string."""
        return self._client_type.value

    @property
    def capabilities(self) -> List[str]:
        """Return list of supported capabilities."""
        caps = []
        if self._capabilities.trace:
            caps.append("trace")
        if self._capabilities.filter:
            caps.append("filter")
        if self._capabilities.history:
            caps.append("history")
        return caps

    @abstractmethod
    def _get_capabilities(self) -> ProtocolCapabilities:
        """Get capabilities for this client type."""
        pass

    @abstractmethod
    def should_receive_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Determine if this client should receive the given event.

        Args:
            event_type: The type of event
            event_data: The event payload data

        Returns:
            True if the client should receive this event
        """
        pass

    def get_auth_metadata(self) -> Dict[str, Any]:
        """Get metadata to include in auth message.

        Returns:
            Dictionary of metadata for auth message
        """
        return {
            "client_version": "0.2.0",
            "platform": self._get_platform(),
        }

    def _get_platform(self) -> str:
        """Get the platform identifier."""
        import sys
        return sys.platform

    def set_event_filters(self, filters: Optional[Dict[str, Any]]) -> None:
        """Set event filters for this adapter.

        Args:
            filters: Filter dictionary with keys like:
                - event_types: List of event types to include
                - sources: List of source IDs to include
                - trace_id: Specific trace ID to filter by
        """
        self._event_filters = filters
        if filters and "trace_id" in filters:
            self._trace_filter = filters["trace_id"]

    def set_trace_filter(self, trace_id: Optional[str]) -> None:
        """Set filter to only show events for a specific trace.

        Args:
            trace_id: The trace ID to filter by, or None to clear
        """
        self._trace_filter = trace_id

    def clear_filters(self) -> None:
        """Clear all event filters."""
        self._event_filters = None
        self._trace_filter = None

    def _matches_trace_filter(self, event_data: Dict[str, Any]) -> bool:
        """Check if event matches the current trace filter.

        Args:
            event_data: The event payload data

        Returns:
            True if event matches (or no filter set)
        """
        if not self._trace_filter:
            return True

        event_trace_id = event_data.get("trace_id")
        return event_trace_id == self._trace_filter

    def _matches_event_type_filter(self, event_type: str) -> bool:
        """Check if event type matches the current filter.

        Args:
            event_type: The type of event

        Returns:
            True if event matches (or no filter set)
        """
        if not self._event_filters:
            return True

        allowed_types = self._event_filters.get("event_types")
        if allowed_types is None:
            return True

        return event_type in allowed_types


class TerminalAdapter(ClientAdapter):
    """Adapter for Terminal (TUI) clients.

    Terminal clients receive all events and support:
    - Full event stream (chat, agent, system events)
    - Event filtering by type and trace_id
    - Command mode switching (/mode chat|event|agent)
    - Trace visualization (/trace <trace_id>)

    Example:
        >>> adapter = TerminalAdapter()
        >>> adapter.set_trace_filter("trace-abc-123")
        >>> # Only events with trace_id="trace-abc-123" will pass
    """

    # High-frequency events that can be filtered in event mode
    HIGH_FREQUENCY_EVENTS = {
        "agent.llm_request",
        "agent.llm_response",
        "agent.thinking_chunk",
    }

    def __init__(self):
        super().__init__(ClientType.TUI)
        self._mode = "chat"  # chat | event | agent
        self._exclude_high_frequency = False

    def _get_capabilities(self) -> ProtocolCapabilities:
        """Terminal clients support all capabilities."""
        return ProtocolCapabilities(trace=True, filter=True, history=True)

    @property
    def mode(self) -> str:
        """Get current display mode."""
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Set the display mode.

        Args:
            mode: One of "chat", "event", "agent"

        Raises:
            ValueError: If mode is invalid
        """
        if mode not in ("chat", "event", "agent"):
            raise ValueError(f"Invalid mode: {mode}. Must be 'chat', 'event', or 'agent'")
        self._mode = mode

    def should_receive_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Determine if terminal should receive this event.

        Terminal clients receive all events by default, but can filter:
        - By trace_id (when set_trace_filter is called)
        - By event type (in agent mode, only agent.* events)
        - High-frequency events (can be excluded)

        Args:
            event_type: The type of event
            event_data: The event payload data

        Returns:
            True if the terminal should display this event
        """
        # Check trace filter first
        if not self._matches_trace_filter(event_data):
            return False

        # Check event type filter
        if not self._matches_event_type_filter(event_type):
            return False

        # Mode-specific filtering
        if self._mode == "chat":
            # Chat mode: messages, agent responses, and important system events
            return (
                event_type.startswith("message.")
                or event_type in ("agent.response", "agent.started", "agent.stopped", "agent.error")
                or event_type.startswith("system.")
                or event_type.startswith("session.")
            )

        elif self._mode == "agent":
            # Agent mode: only agent events
            return event_type.startswith("agent.")

        elif self._mode == "event":
            # Event mode: all events (with optional high-frequency exclusion)
            if self._exclude_high_frequency:
                return event_type not in self.HIGH_FREQUENCY_EVENTS
            return True

        return True

    def set_exclude_high_frequency(self, exclude: bool) -> None:
        """Set whether to exclude high-frequency events in event mode.

        Args:
            exclude: True to exclude high-frequency events
        """
        self._exclude_high_frequency = exclude


class WebAdapter(ClientAdapter):
    """Adapter for Web clients.

    Web clients receive filtered events by default:
    - Excludes high-frequency events (llm_request, llm_response, etc.)
    - Supports trace visualization mode
    - Supports filtered event streams

    Example:
        >>> adapter = WebAdapter()
        >>> adapter.set_trace_filter("trace-abc-123")
        >>> # Will receive all events for this trace, including high-frequency ones
    """

    # Events that are excluded by default for web clients
    EXCLUDED_EVENTS = {
        "agent.llm_request",
        "agent.llm_response",
        "agent.thinking_start",
        "agent.thinking_end",
        "agent.thinking_chunk",
        "tool.execute_start",
        "tool.execute_end",
    }

    def __init__(self):
        super().__init__(ClientType.WEB)
        self._trace_mode = False

    def _get_capabilities(self) -> ProtocolCapabilities:
        """Web clients support trace and history, limited filter."""
        return ProtocolCapabilities(trace=True, filter=True, history=True)

    def should_receive_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Determine if web client should receive this event.

        Web clients filter out high-frequency events by default,
        but show all events when in trace visualization mode.

        Args:
            event_type: The type of event
            event_data: The event payload data

        Returns:
            True if the web client should receive this event
        """
        # Check trace filter first
        if not self._matches_trace_filter(event_data):
            return False

        # Check event type filter
        if not self._matches_event_type_filter(event_type):
            return False

        # In trace mode, show all events for the trace
        if self._trace_mode or self._trace_filter:
            return True

        # Default: exclude high-frequency events
        return event_type not in self.EXCLUDED_EVENTS

    def set_trace_mode(self, enabled: bool) -> None:
        """Enable/disable trace visualization mode.

        When enabled, all events (including high-frequency) are received.

        Args:
            enabled: True to enable trace mode
        """
        self._trace_mode = enabled


class TelegramAdapter(ClientAdapter):
    """Adapter for Telegram Bot clients.

    Telegram clients receive only message-related events:
    - message.new
    - message.delivered
    - agent.response (when mentioned)

    They do not receive system events or detailed agent lifecycle events.
    """

    ALLOWED_EVENTS = {
        "message.new",
        "message.delivered",
        "message.edit",
        "agent.response",
    }

    def __init__(self):
        super().__init__(ClientType.TELEGRAM)

    def _get_capabilities(self) -> ProtocolCapabilities:
        """Telegram clients have limited capabilities."""
        return ProtocolCapabilities(trace=False, filter=False, history=False)

    def should_receive_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        """Determine if Telegram client should receive this event.

        Telegram bots only receive message-related events.

        Args:
            event_type: The type of event
            event_data: The event payload data

        Returns:
            True if the Telegram client should receive this event
        """
        return event_type in self.ALLOWED_EVENTS
