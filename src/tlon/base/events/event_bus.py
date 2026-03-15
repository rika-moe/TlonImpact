"""Event Bus for Tlon.

Central event routing system using async publish-subscribe pattern.
"""

import asyncio
from typing import Dict, List, Optional, Callable

from tlon.base.events.event import Event, EventType
from tlon.base.logger import get_logger

logger = get_logger(__name__)


class EventBus:
    """Central event bus for message routing.
    
    Uses async publish-subscribe pattern. Events are dispatched to
    all subscribers of the event type.
    
    Example:
        >>> bus = EventBus()
        >>> await bus.start()
        >>> 
        >>> def handler(event):
        ...     print(f"Received: {event.type}")
        >>> 
        >>> sub_id = bus.subscribe(EventType.MESSAGE_NEW, handler)
        >>> await bus.publish(Event(...))
        >>> 
        >>> bus.unsubscribe(sub_id)
        >>> await bus.stop()
    """
    
    def __init__(self):
        """Initialize the event bus."""
        self._subscribers: Dict[EventType, List[tuple]] = {}
        self._event_queue: asyncio.Queue[Event] = asyncio.Queue()
        self._running = False
        self._processor_task: Optional[asyncio.Task] = None
        self._subscription_counter = 0
    
    async def start(self) -> None:
        """Start the event processor.
        
        Begins processing events from the queue.
        """
        if self._running:
            logger.warning("EventBus already running")
            return
        
        self._running = True
        self._processor_task = asyncio.create_task(self._process_events())
        logger.info("EventBus started")
    
    async def stop(self) -> None:
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
                await asyncio.wait_for(
                    asyncio.shield(self._processor_task), 
                    timeout=5.0
                )
            except asyncio.CancelledError:
                pass  # Expected when task is cancelled
            except asyncio.TimeoutError:
                logger.warning("EventBus processor task timeout during shutdown")
        
        logger.info("EventBus stopped")
    
    def subscribe(
        self, 
        event_type: EventType, 
        handler: Callable[[Event], None]
    ) -> str:
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
        logger.debug(f"Subscribed {subscription_id} to {event_type.value}")
        
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
                    logger.debug(f"Unsubscribed {subscription_id} from {event_type.value}")
                    return True
        
        logger.warning(f"Subscription {subscription_id} not found")
        return False
    
    async def publish(self, event: Event) -> None:
        """Publish an event to the bus.
        
        The event is queued and will be dispatched to subscribers
        asynchronously.
        
        Args:
            event: The event to publish
        """
        if not self._running:
            logger.error(f"[EventBus] Cannot publish event {event.type.value}: EventBus is not running")
            return
        
        await self._event_queue.put(event)
        logger.info(f"[EventBus] Event queued: {event.type.value} from {event.source} (queue size: {self._event_queue.qsize()})")
    
    async def _process_events(self) -> None:
        """Event processing loop.
        
        Continuously processes events from the queue and dispatches
        them to subscribers.
        """
        logger.info("[EventBus] Event processor started")
        try:
            while self._running:
                try:
                    # Wait for an event with timeout to allow checking _running
                    event = await asyncio.wait_for(
                        self._event_queue.get(), 
                        timeout=0.5
                    )
                    logger.info(f"[EventBus] Processing event from queue: {event.type.value}")
                    await self._dispatch(event)
                    self._event_queue.task_done()
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    # Task was cancelled, exit cleanly
                    logger.info("[EventBus] Event processor cancelled")
                    break
                except Exception as e:
                    logger.error(f"[EventBus] Error in event processing loop: {e}", exc_info=True)
        except asyncio.CancelledError:
            # Exit cleanly when cancelled
            logger.info("[EventBus] Event processor cancelled (outer)")
        logger.info("[EventBus] Event processor stopped")
    
    async def _dispatch(self, event: Event) -> None:
        """Dispatch an event to all subscribers.
        
        Args:
            event: The event to dispatch
        """
        handlers = self._subscribers.get(event.type, [])
        
        if not handlers:
            logger.warning(f"[EventBus] No handlers for event type: {event.type.value}")
            return
        
        logger.info(f"[EventBus] Dispatching {event.type.value} to {len(handlers)} handlers")
        handler_names = [sid for sid, _ in handlers]
        logger.info(f"[EventBus] Handler IDs: {handler_names}")
        
        for subscription_id, handler in handlers:
            try:
                handler_name = getattr(handler, '__name__', str(handler))
                logger.info(f"[EventBus] Calling handler {subscription_id} ({handler_name}) for {event.type.value}")
                if asyncio.iscoroutinefunction(handler):
                    # Schedule async handler as a task
                    asyncio.create_task(
                        self._run_handler(handler, event, subscription_id)
                    )
                    logger.info(f"[EventBus] Scheduled async handler {subscription_id}")
                else:
                    # Run sync handler in thread pool
                    await asyncio.get_event_loop().run_in_executor(
                        None, handler, event
                    )
            except Exception as e:
                logger.error(
                    f"[EventBus] Error dispatching event to {subscription_id}: {e}",
                    exc_info=True
                )
    
    async def _run_handler(
        self, 
        handler: Callable[[Event], None], 
        event: Event,
        subscription_id: str
    ) -> None:
        """Run an async handler with error handling.
        
        Args:
            handler: The async handler function
            event: The event to handle
            subscription_id: For logging purposes
        """
        handler_name = getattr(handler, '__name__', str(handler))
        logger.info(f"[EventBus] Running async handler {subscription_id} ({handler_name}) for {event.type.value}")
        try:
            await handler(event)
            logger.info(f"[EventBus] Handler {subscription_id} completed successfully")
        except Exception as e:
            logger.error(
                f"[EventBus] Error in handler {subscription_id}: {e}",
                exc_info=True
            )
    
    def get_subscriber_count(self, event_type: Optional[EventType] = None) -> int:
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
