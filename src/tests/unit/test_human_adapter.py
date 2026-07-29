"""Unit tests for HumanAdapter and AdapterManager (docs/design/members.md 阶段 B).

R1b mail bridge: speak() parses @mentions from the content, resolves
them to recipient addresses, and publishes MAIL_NEW; a mail with zero
resolvable recipients is rejected.

Covers:
- @mention extraction from message content
- Adapter lifecycle: attach creates, same-name sessions share, last
  detach destroys
- Speaking: address resolution, MAIL_NEW publish, zero-recipient error
- resolve_address (agent id / agent name / online human / full address)
"""

import asyncio
from collections.abc import AsyncGenerator

import pytest

from tlon.base.config import AgentConfig
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.organization.mail.mail import Mail
from tlon.organization.messaging.human_adapter import (
    AdapterManager,
    HumanAdapter,
    parse_mentions,
)


class MockAgentInfoProvider:
    """Mock AgentInfoProvider for testing."""

    def __init__(self, configs: dict[str, AgentConfig]):
        self._configs = configs

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)

    def get_all_ids(self) -> set[str]:
        return set(self._configs.keys())


@pytest.fixture
def provider() -> MockAgentInfoProvider:
    return MockAgentInfoProvider(
        {"xie-yunshu": AgentConfig(id="xie-yunshu", name="谢云舒")}
    )


@pytest.fixture
async def bus() -> AsyncGenerator[EventBus]:
    """Provide a running EventBus and shut it down after the test."""
    event_bus = EventBus()
    await event_bus.initialize()
    yield event_bus
    await event_bus.shutdown()


@pytest.fixture
async def manager(
    bus: EventBus, provider: MockAgentInfoProvider
) -> AsyncGenerator[AdapterManager]:
    """Provide an initialized AdapterManager."""
    mgr = AdapterManager(event_bus=bus, agent_info_provider=provider)
    await mgr.initialize()
    yield mgr
    await mgr.shutdown()


async def _collect_mail(bus: EventBus) -> list[Mail]:
    """Subscribe to MAIL_NEW and return the collecting list."""
    collected: list[Mail] = []

    async def handler(event: Event) -> None:
        collected.append(event.payload)

    bus.subscribe(EventType.MAIL_NEW, handler)
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
        adapter = HumanAdapter("alice", bus, lambda name: None)
        assert adapter.member_id == "human:alice"
        assert adapter.member_type == "adapter"
        assert adapter.address == "alice@tlon.internal"
        assert adapter.username == "alice"

    def test_state_tracks_sessions(self, bus: EventBus):
        adapter = HumanAdapter("alice", bus, lambda name: None)
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
    """Tests for speaking through adapters (R1b @ bridge)."""

    async def test_publish_by_session(self, bus: EventBus, manager: AdapterManager):
        collected = await _collect_mail(bus)
        await manager.attach("alice", "tui-0001")

        mail = await manager.publish("tui-0001", "@谢云舒 hello")
        await _drain(bus)

        assert mail is not None
        assert mail.from_address == "alice@tlon.internal"
        assert mail.to == ["xie-yunshu@tlon.internal"]
        assert mail.content == "@谢云舒 hello"
        assert collected[-1].id == mail.id

    async def test_publish_resolves_explicit_mentions(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001")

        mail = await manager.publish(
            "tui-0001", "hi", mentions=["谢云舒", "xie-yunshu@tlon.internal"]
        )

        assert mail is not None
        # Id and full-address forms dedupe to one recipient
        assert mail.to == ["xie-yunshu@tlon.internal"]

    async def test_publish_zero_recipients_raises(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001")

        with pytest.raises(ValueError, match="收件人"):
            await manager.publish("tui-0001", "hello, anyone?")

    async def test_publish_unresolvable_mentions_raise(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001")

        with pytest.raises(ValueError, match="收件人"):
            await manager.publish("tui-0001", "@不存在的人 hi")

    async def test_publish_unknown_session_rejected(self, manager: AdapterManager):
        mail = await manager.publish("tui-9999", "hello")
        assert mail is None


class TestResolveAddress:
    """Tests for the mention → address bridge."""

    async def test_agent_id_and_name(self, manager: AdapterManager):
        assert manager.resolve_address("xie-yunshu") == "xie-yunshu@tlon.internal"
        assert manager.resolve_address("谢云舒") == "xie-yunshu@tlon.internal"

    async def test_full_address_passthrough(self, manager: AdapterManager):
        assert (
            manager.resolve_address("all@tlon.internal") == "all@tlon.internal"
        )

    async def test_online_human(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001")

        assert manager.resolve_address("alice") == "alice@tlon.internal"
        assert manager.resolve_address("human:alice") == "alice@tlon.internal"
        assert manager.resolve_address("offline-user") is None

    async def test_username_for(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001")

        assert manager.username_for("tui-0001") == "alice"
        assert manager.username_for("tui-9999") is None
