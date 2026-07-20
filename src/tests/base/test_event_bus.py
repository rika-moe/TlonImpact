"""Regression tests for EventBus dispatch semantics.

Covers the fix for fire-and-forget async handlers: dispatch must be
sequential (publish order preserved, no overlapping handler executions)
and a failing handler must not affect other handlers or later events.
"""

import asyncio
from collections.abc import AsyncGenerator

import pytest

from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus


@pytest.fixture
async def bus() -> AsyncGenerator[EventBus]:
    """Provide a running EventBus and shut it down after the test."""
    event_bus = EventBus()
    await event_bus.initialize()
    yield event_bus
    await event_bus.shutdown()


async def _drain(bus: EventBus) -> None:
    """Wait until all queued events have been dispatched."""
    await asyncio.wait_for(bus._event_queue.join(), timeout=5.0)


def _make_event(seq: int) -> Event:
    """Create a MESSAGE_NEW event carrying a sequence number."""
    return Event(type=EventType.MESSAGE_NEW, payload={"seq": seq}, source="test")


async def test_async_handlers_run_sequentially_in_publish_order(bus: EventBus) -> None:
    """A single async subscriber must see events in publish order, one at a time."""
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

    bus.subscribe(EventType.MESSAGE_NEW, handler)

    count = 5
    for seq in range(count):
        await bus.publish(_make_event(seq))
    await _drain(bus)

    assert completed == list(range(count))
    assert max_concurrent == 1


async def test_handler_exception_is_isolated(bus: EventBus) -> None:
    """A raising handler must not block other handlers or subsequent events."""
    good_calls: list[int] = []

    async def bad_handler(event: Event) -> None:
        raise RuntimeError("boom")

    async def good_handler(event: Event) -> None:
        good_calls.append(event.payload["seq"])

    bus.subscribe(EventType.MESSAGE_NEW, bad_handler)
    bus.subscribe(EventType.MESSAGE_NEW, good_handler)

    for seq in range(3):
        await bus.publish(_make_event(seq))
    await _drain(bus)

    # Later handler still ran for every event, including those after the
    # first failure.
    assert good_calls == [0, 1, 2]
    assert bus.is_running()
