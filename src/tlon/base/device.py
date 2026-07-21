"""Device interface for objects that interact with EventBus.

Device provides EventBus interaction capabilities independent of Component lifecycle.
It manages subscriptions and provides a clean API for publishing events.

Key Design: Device and Component are orthogonal concepts.
- Device = "Can talk to EventBus" (subscribe, emit, publish)
- Component = "Managed by ComponentRegistry" (initialize, shutdown, DAG)

A class can be:
- Only Component: MessageStore (no EventBus interaction)
- Only Device: TraceCollector (runtime event handler, not registry-managed)
- Both: NotificationService (registry-managed + EventBus interaction)

Example:
    # Only Device (runtime use)
    class TempHandler(Device):
        async def handle(self, event: Event) -> None:
            await self.emit(EventType.CUSTOM, {"data": ...})

    # Both Component and Device
    class NotificationService(Component, Device):
        async def initialize(self) -> None:
            self.subscribe(EventType.MESSAGE_NEW, self._on_message)
            await super().initialize()  # Component initialize

        async def shutdown(self) -> None:
            self.unsubscribe_all()  # Device cleanup
            await super().shutdown()  # Component shutdown
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from tlon.base.events.event import Event, EventType
from tlon.base.logger import get_logger

if TYPE_CHECKING:
    from tlon.base.events.event_bus import EventBus

logger = get_logger(__name__)

# Type alias for event handlers
EventHandler = Callable[[Event], None] | Callable[[Event], Coroutine[Any, Any, None]]


class Device:
    """Base class for objects that interact with EventBus.

    Device is NOT a Component. It knows nothing about DAG architecture
    or ComponentRegistry. It only cares about EventBus operations.

    Responsibilities:
    - Subscribe/unsubscribe to events
    - Publish/emit events
    - Track subscriptions for cleanup

    Lifecycle (caller-managed, NOT registry-managed):
    - Construction: Store EventBus reference
    - Operation: Subscribe, handle, publish as needed
    - Cleanup: Call unsubscribe_all() when done (optional but recommended)

    Note: If this Device is also a Component, its Component.shutdown()
    should call unsubscribe_all() to clean up EventBus subscriptions.
    """

    def __init__(
        self,
        *,
        event_bus: EventBus,
        name: str | None = None,
    ):
        """Initialize the device.

        Args:
            event_bus: Event bus for publishing/subscribing (required)
            name: Optional device name for logging (defaults to class name)
        """
        self._name = name or self.__class__.__name__
        self._event_bus = event_bus
        self._subscriptions: dict[str, str] = {}
        # Maps internal ID -> EventBus subscription ID

    @property
    def name(self) -> str:
        """Get device name."""
        return self._name

    @property
    def event_bus(self) -> EventBus:
        """Get the EventBus instance."""
        return self._event_bus

    # ========== Subscription Management ==========

    def subscribe(
        self,
        event_type: EventType,
        handler: EventHandler,
    ) -> str:
        """Subscribe to an event type.

        Args:
            event_type: The type of event to subscribe to
            handler: Callback function for events

        Returns:
            Subscription ID for manual unsubscribe

        Example:
            >>> self.subscribe(EventType.MESSAGE_NEW, self._on_message)
        """
        sub_id = self._event_bus.subscribe(event_type, handler)
        internal_id = f"{event_type.value}:{len(self._subscriptions)}"
        self._subscriptions[internal_id] = sub_id
        logger.debug("event_subscribed", name=self.name, event_type=event_type.value)
        return internal_id

    def unsubscribe(self, internal_id: str) -> bool:
        """Unsubscribe from a specific subscription.

        Args:
            internal_id: The subscription ID returned by subscribe()

        Returns:
            True if unsubscribed successfully
        """
        if internal_id not in self._subscriptions:
            return False

        sub_id = self._subscriptions.pop(internal_id)
        self._event_bus.unsubscribe(sub_id)
        logger.debug("event_unsubscribed", name=self.name, internal_id=internal_id)
        return True

    def unsubscribe_all(self) -> None:
        """Unsubscribe from all events.

        Call this when the Device is no longer needed.
        If this Device is also a Component, call this in shutdown().
        """
        for internal_id in list(self._subscriptions.keys()):
            self.unsubscribe(internal_id)

    # ========== Event Publishing ==========

    async def publish(self, event: Event) -> None:
        """Publish an event to the EventBus.

        Args:
            event: The event to publish
        """
        await self._event_bus.publish(event)

    async def emit(
        self,
        event_type: EventType,
        payload: Any,
        *,
        source: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> None:
        """Emit an event with the specified type and payload.

        This is a convenience method that creates an Event and publishes it.

        Args:
            event_type: The type of event
            payload: Event data (should be JSON-serializable)
            source: Event source (defaults to device name)
            trace_id: Optional trace ID for distributed tracing
            span_id: Optional span ID for the current operation
            parent_span_id: Optional parent span ID

        Example:
            >>> await self.emit(
            ...     EventType.AGENT_RESPONSE,
            ...     {"content": "Hello", "agent_id": self.id},
            ... )
        """
        event = Event(
            type=event_type,
            payload=payload,
            source=source or self.name,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
        )
        await self._event_bus.publish(event)
