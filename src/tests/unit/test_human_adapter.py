"""Unit tests for HumanAdapter and AdapterManager (docs/design/members.md 阶段 B).

R2 protocol: speak() accepts explicit to/cc/subject fields; explicit
addressing is the only entry point (the R1b mentions alias and the
legacy @名字 content bridge were removed in PR-C). Recipients are
resolved to addresses and deduplicated. A mail with zero resolvable
recipients is rejected.

Covers:
- Adapter lifecycle: attach creates, same-name sessions share, last
  detach destroys
- Speaking: to/cc/subject, recipient resolution, MAIL_NEW publish,
  zero-recipient error
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
    """Tests for speaking through adapters (R2 to/cc/subject)."""

    async def test_publish_by_session(self, bus: EventBus, manager: AdapterManager):
        collected = await _collect_mail(bus)
        await manager.attach("alice", "tui-0001")

        mail = await manager.publish("tui-0001", "hello", to=["谢云舒"])
        await _drain(bus)

        assert mail is not None
        assert mail.from_address == "alice@tlon.internal"
        assert mail.to == ["xie-yunshu@tlon.internal"]
        assert mail.content == "hello"
        assert collected[-1].id == mail.id

    async def test_publish_explicit_to(self, manager: AdapterManager):
        """R2: explicit to resolves names, ids and full addresses."""
        await manager.attach("alice", "tui-0001")

        mail = await manager.publish(
            "tui-0001", "hi", to=["谢云舒", "xie-yunshu@tlon.internal"]
        )

        assert mail is not None
        # Name and full-address forms dedupe to one recipient
        assert mail.to == ["xie-yunshu@tlon.internal"]

    async def test_publish_cc_and_subject(self, manager: AdapterManager):
        """R2: cc resolves like to; subject is passed through."""
        await manager.attach("alice", "tui-0001")
        await manager.attach("friend", "tui-0002")

        mail = await manager.publish(
            "tui-0001",
            "hi",
            to=["xie-yunshu"],
            cc=["friend"],
            subject="周报",
        )

        assert mail is not None
        assert mail.to == ["xie-yunshu@tlon.internal"]
        assert mail.cc == ["friend@tlon.internal"]
        assert mail.subject == "周报"

    async def test_publish_reply_headers(self, manager: AdapterManager):
        """R2: in_reply_to / thread_id pass through to the created mail."""
        await manager.attach("alice", "tui-0001")
        await manager.attach("friend", "tui-0002")

        original = await manager.publish("tui-0002", "hello", to=["alice"])
        assert original is not None

        reply = await manager.publish(
            "tui-0001",
            "re: hello",
            to=["friend"],
            in_reply_to=original.id,
            thread_id=original.thread_id,
        )

        assert reply is not None
        assert reply.in_reply_to == original.id
        assert reply.thread_id == original.thread_id
        assert reply.id != original.id

    async def test_publish_new_thread_defaults_thread_id(
        self, manager: AdapterManager
    ):
        """Without reply headers the thread root is the mail's own id."""
        await manager.attach("alice", "tui-0001")

        mail = await manager.publish("tui-0001", "hi", to=["xie-yunshu"])

        assert mail is not None
        assert mail.in_reply_to is None
        assert mail.thread_id == mail.id

    async def test_publish_to_and_cc_both_addressed(
        self, manager: AdapterManager
    ):
        """R2: to and cc resolve independently and both count."""
        await manager.attach("alice", "tui-0001")
        await manager.attach("friend", "tui-0002")

        mail = await manager.publish(
            "tui-0001",
            "hi",
            to=["friend"],
            cc=["谢云舒"],
        )

        assert mail is not None
        assert mail.to == ["friend@tlon.internal"]
        assert mail.cc == ["xie-yunshu@tlon.internal"]

    async def test_publish_cc_only_is_addressed(self, manager: AdapterManager):
        """A cc-only mail counts as addressed."""
        await manager.attach("alice", "tui-0001")

        mail = await manager.publish("tui-0001", "fyi", cc=["xie-yunshu"])

        assert mail is not None
        assert mail.to == []
        assert mail.cc == ["xie-yunshu@tlon.internal"]

    async def test_publish_zero_recipients_raises(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001")

        with pytest.raises(ValueError, match="收件人"):
            await manager.publish("tui-0001", "hello, anyone?")

    async def test_publish_unresolvable_recipients_raise(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001")

        with pytest.raises(ValueError, match="收件人"):
            await manager.publish("tui-0001", "hi", to=["不存在的人"])

        with pytest.raises(ValueError, match="收件人"):
            await manager.publish("tui-0001", "@不存在的人 hi")

    async def test_publish_unknown_session_rejected(self, manager: AdapterManager):
        mail = await manager.publish("tui-9999", "hello")
        assert mail is None


class TestResolveAddress:
    """Tests for recipient name → address resolution."""

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
