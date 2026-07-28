"""Unit tests for HumanAdapter and AdapterManager (docs/design/members.md 阶段 B).

Covers:
- @mention extraction from message content
- Adapter lifecycle: attach creates, same-name sessions share, last
  detach destroys
- Speaking: identity format, mention merge, publish via session
- Human resolution helpers (resolve_human / is_human_member)
"""

import asyncio
from collections.abc import AsyncGenerator

import pytest

from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.organization.messaging.human_adapter import (
    AdapterManager,
    HumanAdapter,
    parse_mentions,
)
from tlon.organization.messaging.message import Message


@pytest.fixture
async def bus() -> AsyncGenerator[EventBus]:
    """Provide a running EventBus and shut it down after the test."""
    event_bus = EventBus()
    await event_bus.initialize()
    yield event_bus
    await event_bus.shutdown()


@pytest.fixture
async def manager(bus: EventBus) -> AsyncGenerator[AdapterManager]:
    """Provide an initialized AdapterManager."""
    mgr = AdapterManager(event_bus=bus)
    await mgr.initialize()
    yield mgr
    await mgr.shutdown()


async def _collect_messages(bus: EventBus) -> list[Message]:
    """Subscribe to MESSAGE_NEW and return the collecting list."""
    collected: list[Message] = []

    async def handler(event: Event) -> None:
        collected.append(event.payload)

    bus.subscribe(EventType.MESSAGE_NEW, handler)
    return collected


async def _drain(bus: EventBus) -> None:
    """Wait until all queued events have been dispatched."""
    await asyncio.wait_for(bus._event_queue.join(), timeout=5.0)


class TestParseMentions:
    """Tests for @mention extraction from content."""

    def test_extracts_names(self):
        assert parse_mentions("@谢云舒 你好 @alice") == ["谢云舒", "alice"]

    def test_no_mentions(self):
        assert parse_mentions("hello world") == []

    def test_stops_at_whitespace(self):
        assert parse_mentions("@alice bob") == ["alice"]


class TestHumanAdapterIdentity:
    """Tests for the Member interface of HumanAdapter."""

    def test_member_interface(self, bus: EventBus):
        adapter = HumanAdapter("alice", bus)
        assert adapter.member_id == "human:alice"
        assert adapter.member_type == "adapter"
        assert adapter.triggers_proactive is True
        assert adapter.username == "alice"

    def test_state_tracks_sessions(self, bus: EventBus):
        adapter = HumanAdapter("alice", bus)
        assert adapter.get_state() == "offline"
        adapter.attach_session("tui-0001")
        assert adapter.get_state() == "online"
        adapter.detach_session("tui-0001")
        assert adapter.get_state() == "offline"


class TestAdapterManagerLifecycle:
    """Tests for attach/detach refcount semantics."""

    async def test_attach_creates_adapter(self, manager: AdapterManager):
        adapter = await manager.attach("alice", "tui-0001")
        assert adapter.member_id == "human:alice"
        assert adapter.is_initialized
        assert manager.get_adapter("alice") is adapter
        assert [a.username for a in manager.list_online()] == ["alice"]

    async def test_same_username_shares_adapter(self, manager: AdapterManager):
        first = await manager.attach("alice", "tui-0001")
        second = await manager.attach("alice", "tui-0002")
        assert first is second
        assert second.session_count == 2

    async def test_last_detach_destroys_adapter(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001")
        await manager.attach("alice", "tui-0002")

        await manager.detach("tui-0001")
        adapter = manager.get_adapter("alice")
        assert adapter is not None
        assert adapter.session_count == 1

        await manager.detach("tui-0002")
        assert manager.get_adapter("alice") is None
        assert manager.list_online() == []

    async def test_detach_unknown_session_is_noop(self, manager: AdapterManager):
        await manager.detach("tui-9999")  # must not raise
        assert manager.list_online() == []


class TestAdapterManagerPublish:
    """Tests for speaking through adapters."""

    async def test_publish_by_session(self, bus: EventBus, manager: AdapterManager):
        collected = await _collect_messages(bus)
        await manager.attach("alice", "tui-0001")

        message = await manager.publish("tui-0001", "hello")
        await _drain(bus)

        assert message is not None
        assert message.sender_id == "human:alice"
        assert message.sender_name == "alice"
        assert message.sender_type == "human"
        assert collected[-1].id == message.id

    async def test_publish_parses_at_mentions_from_content(
        self, bus: EventBus, manager: AdapterManager
    ):
        await manager.attach("alice", "tui-0001")

        message = await manager.publish("tui-0001", "@谢云舒 聊聊")

        assert message is not None
        # Raw names are kept; name→ID resolution is the router's job
        assert message.mentions == ["谢云舒"]

    async def test_publish_merges_and_dedupes_mentions(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001")

        message = await manager.publish("tui-0001", "@李白 hi", mentions=["李白", "other"])

        assert message is not None
        assert message.mentions == ["李白", "other"]

    async def test_publish_unknown_session_rejected(self, manager: AdapterManager):
        message = await manager.publish("tui-9999", "hello")
        assert message is None


class TestHumanResolution:
    """Tests for resolve_human / is_human_member."""

    async def test_resolve_human(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001")

        assert manager.resolve_human("alice") == "human:alice"
        assert manager.resolve_human("human:alice") == "human:alice"
        assert manager.resolve_human("offline-user") is None

    async def test_is_human_member(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001")

        assert manager.is_human_member("human:alice") is True
        assert manager.is_human_member("xie-yunshu") is False
        assert manager.is_human_member("human") is False
        assert manager.is_human_member("human:offline") is False

    async def test_username_for(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001")

        assert manager.username_for("tui-0001") == "alice"
        assert manager.username_for("tui-9999") is None
