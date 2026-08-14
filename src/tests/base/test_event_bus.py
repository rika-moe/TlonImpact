"""Tests for EventBus per-subscriber dispatch semantics.

Each subscription has its own bounded queue and consumer task:
- per-subscriber FIFO ordering, one handler at a time per subscription;
- a slow handler never blocks other subscribers;
- handler exceptions are isolated;
- unsubscribe cancels the consumer and discards queued events;
- publish on a stopped bus raises RuntimeError (fail fast);
- a full subscriber queue drops the new event (keeps the old ones);
- shutdown stops all consumer tasks within a bounded wait.
"""

import asyncio
from collections.abc import AsyncGenerator, Callable

import pytest

from tlon.base.events import event_bus as event_bus_module
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus


@pytest.fixture
async def bus() -> AsyncGenerator[EventBus]:
    """Provide a running EventBus and shut it down after the test."""
    event_bus = EventBus()
    await event_bus.initialize()
    yield event_bus
    await event_bus.shutdown()


async def _drain(bus: EventBus, timeout: float = 5.0) -> None:
    """Wait until every subscriber queue has been fully consumed."""
    queues = [
        subscription.queue
        for subscriptions in bus._subscribers.values()
        for subscription in subscriptions
    ]
    await asyncio.wait_for(asyncio.gather(*(q.join() for q in queues)), timeout=timeout)


def _make_event(seq: int) -> Event:
    """Create a MAIL_NEW event carrying a sequence number."""
    return Event(type=EventType.MAIL_NEW, payload={"seq": seq}, source="test")


async def test_per_subscriber_fifo_order_one_at_a_time(bus: EventBus) -> None:
    """One subscriber sees events in publish order, never overlapping."""
    completed: list[int] = []
    running = 0
    max_concurrent = 0

    async def handler(event: Event) -> None:
        nonlocal running, max_concurrent
        running += 1
        max_concurrent = max(max_concurrent, running)
        await asyncio.sleep(0.01)
        running -= 1
        completed.append(event.payload["seq"])

    bus.subscribe(EventType.MAIL_NEW, handler)

    count = 5
    for seq in range(count):
        await bus.publish(_make_event(seq))
    await _drain(bus)

    assert completed == list(range(count))
    assert max_concurrent == 1


async def test_slow_handler_does_not_block_other_subscribers(bus: EventBus) -> None:
    """A stalled handler only backs up its own queue; others keep up."""
    gate = asyncio.Event()
    slow_processed: list[int] = []
    fast_processed: list[int] = []

    async def slow_handler(event: Event) -> None:
        await gate.wait()
        slow_processed.append(event.payload["seq"])

    async def fast_handler(event: Event) -> None:
        fast_processed.append(event.payload["seq"])

    bus.subscribe(EventType.MAIL_NEW, slow_handler)
    bus.subscribe(EventType.MAIL_NEW, fast_handler)

    for seq in range(3):
        await bus.publish(_make_event(seq))

    # The fast subscriber drains all events while the slow one is still
    # stuck on the first one.
    await asyncio.wait_for(_wait_until(lambda: len(fast_processed) == 3), timeout=2.0)
    assert fast_processed == [0, 1, 2]
    assert slow_processed == []

    gate.set()
    await _drain(bus)
    assert slow_processed == [0, 1, 2]


async def test_handler_exception_is_isolated(bus: EventBus) -> None:
    """A raising handler affects neither other subscribers nor its own later events."""
    good_calls: list[int] = []
    bad_calls: list[int] = []

    async def bad_handler(event: Event) -> None:
        bad_calls.append(event.payload["seq"])
        raise RuntimeError("boom")

    async def good_handler(event: Event) -> None:
        good_calls.append(event.payload["seq"])

    bus.subscribe(EventType.MAIL_NEW, bad_handler)
    bus.subscribe(EventType.MAIL_NEW, good_handler)

    for seq in range(3):
        await bus.publish(_make_event(seq))
    await _drain(bus)

    assert bad_calls == [0, 1, 2]
    assert good_calls == [0, 1, 2]
    assert bus.is_running()


async def test_sync_handler_runs_in_thread_pool_in_order(bus: EventBus) -> None:
    """Sync handlers keep per-subscriber FIFO order (thread pool, awaited)."""
    completed: list[int] = []

    def sync_handler(event: Event) -> None:
        completed.append(event.payload["seq"])

    bus.subscribe(EventType.MAIL_NEW, sync_handler)

    for seq in range(3):
        await bus.publish(_make_event(seq))
    await _drain(bus)

    assert completed == [0, 1, 2]


async def test_unsubscribe_discards_in_flight_events(bus: EventBus) -> None:
    """Unsubscribing cancels the consumer; queued events are dropped."""
    gate = asyncio.Event()
    processed: list[int] = []

    async def blocking_handler(event: Event) -> None:
        await gate.wait()
        processed.append(event.payload["seq"])

    subscription_id = bus.subscribe(EventType.MAIL_NEW, blocking_handler)

    for seq in range(3):
        await bus.publish(_make_event(seq))
    # Let the consumer pick up the first event and block on it.
    await asyncio.sleep(0.05)

    assert bus.unsubscribe(subscription_id) is True

    gate.set()
    await asyncio.sleep(0.05)

    # The in-flight handler was cancelled; the two queued events were discarded.
    assert processed == []
    assert bus.get_subscriber_count(EventType.MAIL_NEW) == 0


async def test_unsubscribe_unknown_id_returns_false(bus: EventBus) -> None:
    """Unsubscribing an unknown subscription ID returns False."""
    assert bus.unsubscribe("sub_999") is False


async def test_publish_before_initialize_raises() -> None:
    """publish on a bus that was never started fails fast."""
    bus = EventBus()
    with pytest.raises(RuntimeError, match="not running"):
        await bus.publish(_make_event(0))


async def test_publish_after_shutdown_raises() -> None:
    """publish after shutdown fails fast instead of silently dropping."""
    bus = EventBus()
    await bus.initialize()
    await bus.shutdown()
    with pytest.raises(RuntimeError, match="not running"):
        await bus.publish(_make_event(0))


async def test_subscriber_queue_overflow_drops_new_events(
    bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full subscriber queue drops the new event and keeps queued ones."""
    monkeypatch.setattr(event_bus_module, "SUBSCRIBER_QUEUE_MAX_SIZE", 2)

    gate = asyncio.Event()
    processed: list[int] = []

    async def blocking_handler(event: Event) -> None:
        await gate.wait()
        processed.append(event.payload["seq"])

    # Re-subscribe after monkeypatching so the queue picks up the small cap.
    bus.subscribe(EventType.MAIL_NEW, blocking_handler)

    # publish has no awaits: all five puts land before the consumer runs,
    # so the queue deterministically holds [0, 1] and 2..4 are dropped.
    for seq in range(5):
        await bus.publish(_make_event(seq))

    gate.set()
    await _drain(bus)

    assert processed == [0, 1]


async def test_shutdown_waits_for_in_flight_handler(bus: EventBus) -> None:
    """A cooperative handler cancelled at shutdown lets the bus stop cleanly."""
    started = asyncio.Event()
    release = asyncio.Event()
    processed: list[int] = []

    async def blocking_handler(event: Event) -> None:
        started.set()
        await release.wait()
        processed.append(event.payload["seq"])

    bus.subscribe(EventType.MAIL_NEW, blocking_handler)
    await bus.publish(_make_event(0))
    await asyncio.wait_for(started.wait(), timeout=2.0)

    shutdown_task = asyncio.create_task(bus.shutdown())
    await asyncio.sleep(0.05)
    # Shutdown cancels the in-flight handler; it must not hang.
    await asyncio.wait_for(shutdown_task, timeout=2.0)

    assert not bus.is_running()
    assert processed == []


async def test_shutdown_is_bounded_for_stubborn_handler(
    bus: EventBus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A handler that ignores cancellation cannot stall shutdown forever."""
    monkeypatch.setattr(event_bus_module, "SHUTDOWN_TIMEOUT_SECONDS", 0.2)

    started = asyncio.Event()

    async def stubborn_handler(event: Event) -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await asyncio.sleep(3600)  # ignore cancellation, keep hanging

    bus.subscribe(EventType.MAIL_NEW, stubborn_handler)
    await bus.publish(_make_event(0))
    await asyncio.wait_for(started.wait(), timeout=2.0)

    await asyncio.wait_for(bus.shutdown(), timeout=2.0)
    assert not bus.is_running()


async def _wait_until(predicate: Callable[[], bool], interval: float = 0.01) -> None:
    """Poll until the predicate holds (caller wraps with wait_for)."""
    while not predicate():
        await asyncio.sleep(interval)
