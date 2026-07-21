"""Event Bus for Tlon.

Central event routing system using async publish-subscribe pattern.
"""

import asyncio
from collections.abc import Callable

from tlon.base.component import Component
from tlon.base.events.event import Event, EventType
from tlon.base.logger import get_logger

logger = get_logger(__name__)


class EventBus(Component):
    """Central event bus for message routing.

    Uses async publish-subscribe pattern. Events are dispatched to
    all subscribers of the event type.

    This is a core infrastructure component that other components
    depend on for communication. It is created early in the
    initialization sequence.

    Example:
        >>> bus = EventBus()
        >>> await bus.initialize()
        >>>
        >>> def handler(event):
        ...     print(f"Received: {event.type}")
        >>>
        >>> sub_id = bus.subscribe(EventType.MESSAGE_NEW, handler)
        >>> await bus.publish(Event(...))
        >>>
        >>> bus.unsubscribe(sub_id)
        >>> await bus.shutdown()
    """

    def __init__(self):
        """Initialize the event bus."""
        super().__init__(name="EventBus")
        self._subscribers: dict[EventType, list[tuple]] = {}
        self._event_queue: asyncio.Queue[Event] = asyncio.Queue()
        self._running = False
        self._processor_task: asyncio.Task | None = None
        self._subscription_counter = 0

    async def initialize(self) -> None:
        """Start the event processor.

        Begins processing events from the queue.
        """
        if self._running:
            logger.warning("event_bus_already_running")
            return

        self._running = True
        self._processor_task = asyncio.create_task(self._process_events())
        self._initialized = True
        logger.info("event_bus_started")

    async def shutdown(self) -> None:
        """Stop the event processor.

        Waits for current event processing to complete.
        """
        if not self._running:
            return

        self._running = False

        if self._processor_task:
            try:
                # Cancel the processor task
                self._processor_task.cancel()
                # Wait for it to complete (with timeout)
                await asyncio.wait_for(asyncio.shield(self._processor_task), timeout=5.0)
            except asyncio.CancelledError:
                pass  # Expected when task is cancelled
            except TimeoutError:
                logger.warning("event_bus_shutdown_timeout")

        logger.info("event_bus_stopped")

    def subscribe(self, event_type: EventType, handler: Callable[[Event], None]) -> str:
        """Subscribe to events of a specific type.

        Args:
            event_type: The type of event to subscribe to
            handler: Callback function to handle events

        Returns:
            Subscription ID (use this to unsubscribe)
        """
        self._subscription_counter += 1
        subscription_id = f"sub_{self._subscription_counter}"

        if event_type not in self._subscribers:
            self._subscribers[event_type] = []

        self._subscribers[event_type].append((subscription_id, handler))
        logger.debug(
            "event_subscribed", subscription_id=subscription_id, event_type=event_type.value
        )

        return subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Unsubscribe from events.

        Args:
            subscription_id: The subscription ID returned by subscribe()

        Returns:
            True if subscription was found and removed
        """
        for event_type, handlers in self._subscribers.items():
            for idx, (sid, _) in enumerate(handlers):
                if sid == subscription_id:
                    handlers.pop(idx)
                    logger.debug(
                        "event_unsubscribed",
                        subscription_id=subscription_id,
                        event_type=event_type.value,
                    )
                    return True

        logger.warning("subscription_not_found", subscription_id=subscription_id)
        return False

    async def publish(self, event: Event) -> None:
        """Publish an event to the bus.

        The event is queued and will be dispatched to subscribers
        asynchronously.

        Args:
            event: The event to publish
        """
        if not self._running:
            logger.error("event_publish_rejected", event_type=event.type.value)
            return

        await self._event_queue.put(event)
        logger.info(
            "event_queued",
            event_type=event.type.value,
            source=event.source,
            queue_size=self._event_queue.qsize(),
        )

    async def _process_events(self) -> None:
        """Event processing loop.

        Continuously processes events from the queue and dispatches
        them to subscribers.
        """
        logger.info("event_processor_started")
        try:
            while self._running:
                try:
                    # Wait for an event with timeout to allow checking _running
                    event = await asyncio.wait_for(self._event_queue.get(), timeout=0.5)
                    logger.info("event_processing", event_type=event.type.value)
                    await self._dispatch(event)
                    self._event_queue.task_done()
                except TimeoutError:
                    continue
                except asyncio.CancelledError:
                    # Task was cancelled, exit cleanly
                    logger.info("event_processor_cancelled")
                    break
                except Exception as e:
                    logger.error("event_processing_error", error=str(e), exc_info=True)
        except asyncio.CancelledError:
            # Exit cleanly when cancelled
            logger.info("event_processor_cancelled")
        logger.info("event_processor_stopped")

    async def _dispatch(self, event: Event) -> None:
        """Dispatch an event to all subscribers.

        Handlers are invoked sequentially in subscription order. Because the
        event processing loop has a single consumer, each handler is awaited
        before the next one starts: event order is preserved, at most one
        handler runs at a time, and one handler's failure does not affect
        the others.

        Args:
            event: The event to dispatch
        """
        handlers = self._subscribers.get(event.type, [])

        if not handlers:
            logger.warning("event_handler_missing", event_type=event.type.value)
            return

        logger.info(
            "event_dispatching",
            event_type=event.type.value,
            handler_count=len(handlers),
            handler_ids=[sid for sid, _ in handlers],
        )

        for subscription_id, handler in handlers:
            try:
                handler_name = getattr(handler, "__name__", str(handler))
                logger.info(
                    "handler_call",
                    subscription_id=subscription_id,
                    handler=handler_name,
                    event_type=event.type.value,
                )
                if asyncio.iscoroutinefunction(handler):
                    # Await async handler sequentially (errors are isolated
                    # inside _run_handler so later handlers still run)
                    await self._run_handler(handler, event, subscription_id)
                else:
                    # Run sync handler in thread pool
                    await asyncio.get_event_loop().run_in_executor(None, handler, event)
            except Exception as e:
                logger.error(
                    "event_dispatch_error",
                    subscription_id=subscription_id,
                    error=str(e),
                    exc_info=True,
                )

    async def _run_handler(
        self, handler: Callable[[Event], None], event: Event, subscription_id: str
    ) -> None:
        """Run an async handler with error handling.

        Args:
            handler: The async handler function
            event: The event to handle
            subscription_id: For logging purposes
        """
        try:
            await handler(event)
            logger.info("handler_completed", subscription_id=subscription_id)
        except Exception as e:
            logger.error(
                "handler_error", subscription_id=subscription_id, error=str(e), exc_info=True
            )

    def get_subscriber_count(self, event_type: EventType | None = None) -> int:
        """Get the number of subscribers.

        Args:
            event_type: If specified, count only subscribers to this type

        Returns:
            Number of subscribers
        """
        if event_type:
            return len(self._subscribers.get(event_type, []))

        return sum(len(handlers) for handlers in self._subscribers.values())

    def is_running(self) -> bool:
        """Check if the event bus is running.

        Returns:
            True if the event processor is active
        """
        return self._running
