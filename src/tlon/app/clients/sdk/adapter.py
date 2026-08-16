"""Client Adapter Pattern for Tlon SDK.

Adapters encapsulate client-type-specific behavior:
- TerminalAdapter: Declares mode display state and optional high-frequency exclusions

Each adapter provides:
- Client type identification
- Filter DECLARATION (get_effective_filters) — the actual matching is
  enforced by the server (NotificationService), which is the single
  filtering point. Adapters never match events locally.
- Protocol capabilities
- Client-specific metadata
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ClientType(StrEnum):
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


class ClientAdapter(ABC):
    """Abstract base class for client adapters.

    Adapters encapsulate client-type-specific behavior including:
    - Client type identification
    - Filter declaration (matching happens server-side)
    - Protocol capabilities
    - Client-specific metadata
    """

    def __init__(self, client_type: ClientType):
        self._client_type = client_type
        self._capabilities = self._get_capabilities()
        self._event_filters: dict[str, Any] | None = None
        self._trace_filter: str | None = None

    @property
    def client_type(self) -> str:
        """Return the client type string."""
        return self._client_type.value

    @property
    def capabilities(self) -> list[str]:
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

    def get_effective_filters(self) -> dict[str, Any]:
        """Compute the effective filter declaration for this adapter.

        Combines user filters (set_event_filters / set_trace_filter) with
        client-type defaults. The result is sent to the server, which is the
        single filtering point — adapters never match events locally.

        Returns:
            Filter dictionary understood by the server, e.g.:
                - event_types: include list ("prefix.*" wildcard supported)
                - exclude_event_types: exclusion list
                - trace_id: only events carrying this trace ID
        """
        filters: dict[str, Any] = dict(self._event_filters or {})
        if self._trace_filter:
            filters["trace_id"] = self._trace_filter
        return filters

    def get_auth_metadata(self) -> dict[str, Any]:
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

    def set_event_filters(self, filters: dict[str, Any] | None) -> None:
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

    def set_trace_filter(self, trace_id: str | None) -> None:
        """Set filter to only show events for a specific trace.

        Args:
            trace_id: The trace ID to filter by, or None to clear
        """
        self._trace_filter = trace_id

    def clear_filters(self) -> None:
        """Clear all event filters."""
        self._event_filters = None
        self._trace_filter = None


class TerminalAdapter(ClientAdapter):
    """Adapter for Terminal (TUI) clients.

    Terminal clients receive all events allowed by their mode and support:
    - Mode display state (chat | event | agent) — mode semantics are
      enforced server-side; the adapter only tracks the current mode
    - Event filtering by type and trace_id (declared, enforced server-side)
    - Optional high-frequency event exclusion in event mode
    - Trace visualization (/trace <trace_id>)

    Example:
        >>> adapter = TerminalAdapter()
        >>> adapter.set_trace_filter("trace-abc-123")
        >>> adapter.get_effective_filters()
        {'trace_id': 'trace-abc-123'}
    """

    # High-frequency events that can be filtered in event mode
    HIGH_FREQUENCY_EVENTS = {
        "agent.llm_request",
        "agent.llm_response",
        "agent.thinking_chunk",
    }

    def __init__(self) -> None:
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

    def get_effective_filters(self) -> dict[str, Any]:
        """Compute the effective filters for a terminal client.

        Adds the high-frequency exclusion only in event mode (when enabled);
        mode semantics themselves are enforced server-side.
        """
        filters = super().get_effective_filters()
        if self._mode == "event" and self._exclude_high_frequency:
            excluded = set(filters.get("exclude_event_types", [])) | self.HIGH_FREQUENCY_EVENTS
            filters["exclude_event_types"] = sorted(excluded)
        return filters

    def set_exclude_high_frequency(self, exclude: bool) -> None:
        """Set whether to exclude high-frequency events in event mode.

        Takes effect the next time the client syncs filters to the server
        (set_mode / set_filters / set_trace_filter / authenticate).

        Args:
            exclude: True to exclude high-frequency events
        """
        self._exclude_high_frequency = exclude
