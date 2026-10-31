"""Event Bus for Tlon.

Central event routing system using async publish-subscribe pattern.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from tlon.base.component import Component
from tlon.base.events.event import Event, EventType
from tlon.base.execution import bind_execution
from tlon.base.logger import get_logger

logger = get_logger(__name__)

# 订阅者可注册同步或异步处理器；异步处理器在事件循环内 await，同步处理器在线程池运行
EventHandler = Callable[[Event], None | Awaitable[None]]

# 每个订阅者队列的容量上限：慢订阅者只会积自己的队列，溢出时丢新事件
# 并记 event_queue_overflow 警告，防止慢订阅者静默涨内存拖垮整个进程
SUBSCRIBER_QUEUE_MAX_SIZE = 1000

# shutdown 时等待所有订阅者消费 task 停止的有界超时（秒）
SHUTDOWN_TIMEOUT_SECONDS = 5.0


@dataclass
class _Subscription:
    """Internal state for one subscription: handler + dedicated queue + consumer task."""

    subscription_id: str
    event_type: EventType
    handler: EventHandler
    queue: asyncio.Queue[Event] = field(
        default_factory=lambda: asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX_SIZE)
    )
    task: asyncio.Task[None] | None = None


class EventBus(Component):
    """Central event bus for message routing.

    Per-subscriber dispatch: each subscription gets its own bounded
    ``asyncio.Queue`` and a long-lived consumer task. ``publish`` enqueues
    the event into every matching subscriber's queue (O(1), never blocks),
    so a slow handler only backs up its own queue, never the whole bus.

    Ordering guarantee is per-subscriber: events delivered to one
    subscription are handled strictly in publish order (FIFO), one at a
    time. No ordering is guaranteed across different subscribers.

    Overflow semantics: when a subscriber's queue is full
    (``SUBSCRIBER_QUEUE_MAX_SIZE``), the *new* event is dropped and an
    ``event_queue_overflow`` warning is logged; already-queued events are
    kept (mail semantics: lose the new, not the old).

    Error isolation: a raising handler only logs an error; it affects
    neither its own subsequent events nor any other subscriber.

    Unsubscribing cancels the consumer task and discards events still
    queued for that subscription.

    ``publish`` on a bus that is not running raises ``RuntimeError``
    (fail fast) instead of silently dropping the event.

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
        >>> sub_id = bus.subscribe(EventType.MAIL_NEW, handler)
        >>> await bus.publish(Event(...))
        >>>
        >>> bus.unsubscribe(sub_id)
        >>> await bus.shutdown()
    """

    def __init__(self) -> None:
        """Initialize the event bus."""
        super().__init__(name="EventBus")
        self._subscribers: dict[EventType, list[_Subscription]] = {}
        self._running = False
        self._subscription_counter = 0

    async def initialize(self) -> None:
        """Start the event bus.

        Spawns the consumer task for every existing subscription
        (subscriptions created before initialize are supported).
        """
        if self._running:
            logger.warning("event_bus_already_running")
            return

        self._running = True
        for subscriptions in self._subscribers.values():
            for subscription in subscriptions:
                self._start_consumer(subscription)
        self._initialized = True
        logger.info("event_bus_started")

    async def shutdown(self) -> None:
        """Stop the event bus.

        Cancels every subscriber's consumer task and waits for all of
        them to stop, bounded by ``SHUTDOWN_TIMEOUT_SECONDS``. Events
        still queued at shutdown are discarded.
        """
        if not self._running:
            return

        self._running = False

        tasks = [
            subscription.task
            for subscriptions in self._subscribers.values()
            for subscription in subscriptions
            if subscription.task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=SHUTDOWN_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                logger.warning("event_bus_shutdown_timeout", pending=len(tasks))

        logger.info("event_bus_stopped")

    def subscribe(self, event_type: EventType, handler: EventHandler) -> str:
        """Subscribe to events of a specific type.

        Creates a dedicated bounded queue and (when the bus is running)
        a consumer task for this subscription.

        Args:
            event_type: The type of event to subscribe to
            handler: Callback function to handle events

        Returns:
            Subscription ID (use this to unsubscribe)
        """
        self._subscription_counter += 1
        subscription_id = f"sub_{self._subscription_counter}"

        subscription = _Subscription(
            subscription_id=subscription_id,
            event_type=event_type,
            handler=handler,
        )
        if self._running:
            self._start_consumer(subscription)

        if event_type not in self._subscribers:
            self._subscribers[event_type] = []

        self._subscribers[event_type].append(subscription)
        logger.debug(
            "event_subscribed", subscription_id=subscription_id, event_type=event_type.value
        )

        return subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Unsubscribe from events.

        Cancels the subscription's consumer task; events still queued
        for it are discarded.

        Args:
            subscription_id: The subscription ID returned by subscribe()

        Returns:
            True if subscription was found and removed
        """
        for event_type, subscriptions in self._subscribers.items():
            for idx, subscription in enumerate(subscriptions):
                if subscription.subscription_id == subscription_id:
                    subscriptions.pop(idx)
                    if subscription.task is not None:
                        subscription.task.cancel()
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

        The event is enqueued (non-blocking) into the dedicated queue of
        every subscriber of its type. If a subscriber's queue is full,
        the new event is dropped for that subscriber and an
        ``event_queue_overflow`` warning is logged.

        Args:
            event: The event to publish

        Raises:
            RuntimeError: If the bus is not running (fail fast).
        """
        if not self._running:
            raise RuntimeError(f"EventBus is not running; cannot publish {event.type.value}")

        subscriptions = self._subscribers.get(event.type)
        if not subscriptions:
            logger.warning("event_handler_missing", event_type=event.type.value)
            return

        # Snapshot: handlers (sync ones run in threads) may unsubscribe concurrently
        for subscription in list(subscriptions):
            try:
                subscription.queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "event_queue_overflow",
                    subscription_id=subscription.subscription_id,
                    event_type=event.type.value,
                    queue_size=subscription.queue.qsize(),
                )

        logger.info(
            "event_published",
            event_type=event.type.value,
            source=event.source,
            subscriber_count=len(subscriptions),
        )

    def _start_consumer(self, subscription: _Subscription) -> None:
        """Spawn the long-lived consumer task for a subscription."""
        with bind_execution(None):
            subscription.task = asyncio.create_task(self._consume_events(subscription))

    async def _consume_events(self, subscription: _Subscription) -> None:
        """Consume events from one subscription's queue, in FIFO order.

        Runs until cancelled (unsubscribe or shutdown). Handler errors
        are logged and isolated: they never stop this consumer nor any
        other subscriber.
        """
        subscription_id = subscription.subscription_id
        handler = subscription.handler
        logger.debug("event_consumer_started", subscription_id=subscription_id)
        try:
            while True:
                event = await subscription.queue.get()
                try:
                    if asyncio.iscoroutinefunction(handler):
                        result = handler(event)
                        if result is not None:
                            await result
                    else:
                        # Run sync handler in thread pool, awaited so
                        # per-subscriber FIFO order is preserved
                        await asyncio.get_running_loop().run_in_executor(None, handler, event)
                    logger.info("handler_completed", subscription_id=subscription_id)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(
                        "handler_error",
                        subscription_id=subscription_id,
                        error=str(e),
                        exc_info=True,
                    )
                finally:
                    subscription.queue.task_done()
        except asyncio.CancelledError:
            logger.debug("event_consumer_cancelled", subscription_id=subscription_id)

    def get_subscriber_count(self, event_type: EventType | None = None) -> int:
        """Get the number of subscribers.

        Args:
            event_type: If specified, count only subscribers to this type

        Returns:
            Number of subscribers
        """
        if event_type:
            return len(self._subscribers.get(event_type, []))

        return sum(len(subscriptions) for subscriptions in self._subscribers.values())

    def is_running(self) -> bool:
        """Check if the event bus is running.

        Returns:
            True if the bus has been initialized and not yet shut down
        """
        return self._running
