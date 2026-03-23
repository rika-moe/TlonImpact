"""Notification Service for Tlon Server.

Responsible for pushing events to connected client sessions.
Decouples event routing from session management.

Architecture:
    NotificationService is a Device that depends on:
    - EventBus (for event subscription)
    - SessionManager (for session lookup)

    All dependencies are injected via constructor (DAG rule).
"""

import asyncio
from collections.abc import Callable

from tlon.base.component import Component
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger

from .session_manager import ClientSession, SessionManager

logger = get_logger(__name__)


EventHandler = Callable[[Event], None]


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

        logger.info("NotificationService constructed")

    async def initialize(self) -> None:
        """Start the service by subscribing to events."""
        self.subscribe(EventType.MESSAGE_NEW, self._on_message_new)
        # Device has no async initialize, just log
        logger.info("NotificationService initialized")
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
        logger.info("NotificationService shut down")

    def register_handler(self, session_id: str, handler: EventHandler) -> None:
        """Register an event handler for a session.

        Args:
            session_id: The session ID
            handler: Callback function for events
        """
        self._handlers[session_id] = handler
        logger.debug(f"Event handler registered for session: {session_id}")

    def unregister_handler(self, session_id: str) -> None:
        """Unregister an event handler for a session.

        Args:
            session_id: The session ID
        """
        self._handlers.pop(session_id, None)
        logger.debug(f"Event handler unregistered for session: {session_id}")

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

    async def _on_message_new(self, event: Event) -> None:
        """Handle MESSAGE_NEW events from EventBus.

        Pushes messages to eligible client sessions based on their
        mode and filters.

        Args:
            event: The MESSAGE_NEW event
        """

        # Get all sessions from session manager
        sessions = self._session_manager.get_all_sessions()

        for session in sessions:
            # Skip agent mode sessions
            if session.mode == "agent":
                continue

            # Apply filters
            if not self._should_send_to_session(session, event):
                continue

            # Push to this session
            handler = self._handlers.get(session.id)
            if handler:
                await self._safe_call(handler, event)

    def _should_send_to_session(self, session: ClientSession, event: Event) -> bool:
        """Check if an event should be sent to a session.

        Args:
            session: The client session
            event: The event to check

        Returns:
            True if should send
        """
        if not session.filters:
            return True

        # Check agent filter
        if "agent" in session.filters:
            agent_filter = session.filters["agent"]

            # Get source from event
            source = event.source

            if not self._match_filter(source, agent_filter):
                return False

        return True

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
            logger.error(f"Error in event handler: {e}", exc_info=True)

    def get_handler_count(self) -> int:
        """Get the number of registered handlers.

        Returns:
            Number of handlers
        """
        return len(self._handlers)
