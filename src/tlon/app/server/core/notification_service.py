"""Notification Service for Tlon Server.

Responsible for pushing events to connected client sessions.
Decouples event routing from session management.

This service is the SINGLE filtering point for events pushed to clients:
- Mode semantics ("chat" / "event" / "agent") are applied here
- Session filters (event_types, exclude_event_types, trace_id, agent) are applied here

Clients (SDK adapters) only DECLARE their desired filters and send them to the
server (via the auth or filter messages); they never re-filter events locally.

Architecture:
    NotificationService is a Device that depends on:
    - EventBus (for event subscription)
    - SessionManager (for session lookup)

    All dependencies are injected via constructor (DAG rule).
"""

import asyncio
from collections.abc import Callable
from typing import Any

from tlon.base.component import Component
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger

from .session_manager import ClientSession, SessionManager

logger = get_logger(__name__)


EventHandler = Callable[[Event], None]

# Mode-based event type rules — the single source of truth for mode semantics.
# Patterns ending with ".*" match by prefix; other patterns match exactly.
# Modes not listed here (e.g. "event") receive all event types.
MODE_EVENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "chat": (
        "mail.*",
        "agent.response",
        "agent.started",
        "agent.stopped",
        "agent.error",
        "system.*",
    ),
    "agent": ("agent.*",),
}


class NotificationService(Component, Device):
    """Pushes system events to connected clients.

    Responsibilities:
    - Subscribe to EventBus events
    - Filter events per session (based on mode and filters)
    - Push events to registered handlers

    Does NOT handle:
    - Session lifecycle
    - Message routing decisions
    - Message storage

    Dependencies (constructor injected):
        - event_bus: EventBus
        - session_manager: SessionManager

    Example:
        >>> service = NotificationService(
        ...     event_bus=event_bus,
        ...     session_manager=session_manager,
        ... )
        >>> await service.initialize()
        >>> service.register_handler(session_id, handler)
        >>> await service.shutdown()
    """

    def __init__(
        self,
        event_bus: EventBus,
        session_manager: SessionManager,
    ):
        """Initialize the notification service.

        All dependencies are injected via constructor (DAG architecture).

        Args:
            event_bus: Event bus to subscribe to
            session_manager: Session manager for session lookup
        """
        # Initialize both parents
        Component.__init__(self, name="NotificationService")
        Device.__init__(self, event_bus=event_bus, name="NotificationService")

        self._session_manager = session_manager

        # session_id -> handler mapping
        self._handlers: dict[str, EventHandler] = {}

        # Internal lock for handler registration
        self._lock = asyncio.Lock()

        logger.info("notification_service_constructed")

    async def initialize(self) -> None:
        """Start the service by subscribing to all event types."""
        for event_type in EventType:
            self.subscribe(event_type, self._on_event)
        # Device has no async initialize, just log
        logger.info("notification_service_initialized")
        # Mark Component as initialized
        self._initialized = True

    async def shutdown(self) -> None:
        """Stop the service by unsubscribing from events."""
        async with self._lock:
            self._handlers.clear()

        # Cleanup Device subscriptions
        self.unsubscribe_all()
        # Mark Component as shut down
        self._initialized = False
        logger.info("notification_service_shutdown")

    def register_handler(self, session_id: str, handler: EventHandler) -> None:
        """Register an event handler for a session.

        Args:
            session_id: The session ID
            handler: Callback function for events
        """
        self._handlers[session_id] = handler
        logger.debug("event_handler_registered", session_id=session_id)

    def unregister_handler(self, session_id: str) -> None:
        """Unregister an event handler for a session.

        Args:
            session_id: The session ID
        """
        self._handlers.pop(session_id, None)
        logger.debug("event_handler_unregistered", session_id=session_id)

    async def push_to_session(self, session_id: str, event: Event) -> bool:
        """Manually push an event to a specific session.

        Args:
            session_id: Target session ID
            event: Event to push

        Returns:
            True if pushed successfully
        """
        handler = self._handlers.get(session_id)
        if not handler:
            return False

        await self._safe_call(handler, event)
        return True

    async def broadcast(self, event: Event, exclude_session: str | None = None) -> None:
        """Broadcast an event to all registered handlers.

        Args:
            event: Event to broadcast
            exclude_session: Optional session ID to exclude
        """
        handlers = list(self._handlers.items())

        for session_id, handler in handlers:
            if session_id == exclude_session:
                continue
            await self._safe_call(handler, event)

    async def _on_event(self, event: Event) -> None:
        """Handle events from EventBus.

        Pushes events to eligible client sessions based on their
        mode and filters.

        Args:
            event: The event to dispatch
        """
        # Get all sessions from session manager
        sessions = self._session_manager.get_all_sessions()

        for session in sessions:
            # Apply mode semantics and session filters
            if not self._should_send_to_session(session, event):
                continue

            # Push to this session
            handler = self._handlers.get(session.id)
            if handler:
                await self._safe_call(handler, event)

    def _should_send_to_session(self, session: ClientSession, event: Event) -> bool:
        """Check if an event should be sent to a session.

        Filtering pipeline (all steps are ANDed):
        1. Mode semantics: the session mode ("chat" / "agent") restricts the
           event type set; "event" mode imposes no restriction.
        2. Session filters (session.filters), all optional:
           - trace_id: only events carrying this trace ID
           - event_types: include list ("prefix.*" wildcard supported)
           - exclude_event_types: exclusion list ("prefix.*" wildcard supported)
           - agent: event source pattern ("*" wildcard supported)

        Args:
            session: The client session
            event: The event to check

        Returns:
            True if should send
        """
        event_type = event.type.value if isinstance(event.type, EventType) else str(event.type)

        # 1. Mode semantics
        mode_patterns = MODE_EVENT_PATTERNS.get(session.mode)
        if mode_patterns is not None and not self._match_any_event_type(event_type, mode_patterns):
            return False

        filters = session.filters
        if not filters:
            return True

        # 2. Trace filter
        trace_filter = filters.get("trace_id")
        if trace_filter and self._event_trace_id(event) != trace_filter:
            return False

        # 3. Event type include list
        event_types = filters.get("event_types")
        if event_types and not self._match_any_event_type(event_type, event_types):
            return False

        # 4. Event type exclusion list
        excluded_types = filters.get("exclude_event_types")
        if excluded_types and self._match_any_event_type(event_type, excluded_types):
            return False

        # 5. Agent (event source) filter
        if "agent" in filters:
            agent_filter = filters["agent"]
            if not self._match_filter(event.source, agent_filter):
                return False

        return True

    @staticmethod
    def _event_trace_id(event: Event) -> str | None:
        """Extract the trace ID from an event (envelope or dict payload).

        Args:
            event: The event to inspect

        Returns:
            The trace ID, or None if the event carries none
        """
        if event.trace_id:
            return event.trace_id
        if isinstance(event.payload, dict):
            return event.payload.get("trace_id")
        return None

    @staticmethod
    def _match_any_event_type(event_type: str, patterns: Any) -> bool:
        """Match an event type against a list of patterns.

        Pattern syntax:
        - "*" matches everything
        - "prefix.*" matches event types starting with "prefix."
        - anything else matches exactly

        Args:
            event_type: The event type string (e.g. "mail.delivered")
            patterns: Iterable of patterns

        Returns:
            True if any pattern matches
        """
        for pattern in patterns:
            if pattern == "*":
                return True
            if pattern.endswith(".*"):
                if event_type.startswith(pattern[:-1]):
                    return True
            elif event_type == pattern:
                return True
        return False

    def _match_filter(self, value: str, pattern: str) -> bool:
        """Match a value against a filter pattern.

        Supports simple wildcards:
        - "*" matches everything
        - "prefix-*" matches values starting with prefix

        Args:
            value: The value to match
            pattern: The filter pattern

        Returns:
            True if value matches pattern
        """
        if pattern == "*":
            return True
        if "*" in pattern:
            prefix = pattern.replace("*", "")
            return value.startswith(prefix)
        return value == pattern

    async def _safe_call(self, handler: EventHandler, event: Event) -> None:
        """Safely call an event handler with error handling.

        Args:
            handler: The handler to call
            event: The event to pass
        """
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
        except Exception as e:
            logger.error("event_handler_error", error=str(e), exc_info=True)

    def get_handler_count(self) -> int:
        """Get the number of registered handlers.

        Returns:
            Number of handlers
        """
        return len(self._handlers)
