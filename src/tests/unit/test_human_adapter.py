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
- Mail delivery: MAIL_DELIVERED fan-out to attached session sinks
  (single-track delivery, docs/design/members.md 阶段 B)
- resolve_address (agent id / agent name / online or known-offline
  human / full address)
"""

import asyncio
from collections.abc import AsyncGenerator, Callable

import pytest

from tlon.base.config import AgentConfig
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
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
    bus: EventBus, provider: MockAgentInfoProvider, tmp_path
) -> AsyncGenerator[AdapterManager]:
    """Provide an initialized AdapterManager (isolated tlon home)."""
    mgr = AdapterManager(
        event_bus=bus,
        agent_info_provider=provider,
        tlon_paths=TlonPaths(home=tmp_path),
    )
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
    """Wait until all per-subscriber queues have been fully consumed."""
    queues = [
        subscription.queue
        for subscriptions in bus._subscribers.values()
        for subscription in subscriptions
    ]

    async def _join_all() -> None:
        while True:
            await asyncio.gather(*(q.join() for q in queues))
            if all(q.empty() for q in queues):
                return

    await asyncio.wait_for(_join_all(), timeout=5.0)


def _noop_sink(event: Event) -> None:
    """No-op session sink for tests that do not care about delivery."""


def _collecting_sink(collected: list[Event]) -> Callable[[Event], None]:
    """Create a session sink that appends pushed events to a list."""

    def sink(event: Event) -> None:
        collected.append(event)

    return sink


async def _deliver_mail(bus: EventBus, mail: Mail, recipient: str) -> None:
    """Publish a MAIL_DELIVERED event and wait for dispatch."""
    await bus.publish(
        Event(
            type=EventType.MAIL_DELIVERED,
            payload={"mail": mail, "recipient": recipient},
            source="mail-delivery",
        )
    )
    await _drain(bus)


def _make_mail(to: list[str]) -> Mail:
    """Create a minimal mail for delivery tests."""
    return Mail.create(
        from_address="xie-yunshu@tlon.internal", to=to, content="hi"
    )


class TestHumanAdapterIdentity:
    """Tests for the Member interface of HumanAdapter."""

    def test_member_interface(self, bus: EventBus):
        adapter = HumanAdapter("alice", bus, lambda name: None)
        assert adapter.member_id == "human:alice"
        assert adapter.address == "alice@tlon.internal"
        assert adapter.username == "alice"

    def test_state_tracks_sessions(self, bus: EventBus):
        adapter = HumanAdapter("alice", bus, lambda name: None)
        assert adapter.get_state() == "offline"
        adapter.attach_session("tui-0001", _noop_sink)
        assert adapter.get_state() == "online"
        adapter.detach_session("tui-0001")
        assert adapter.get_state() == "offline"


class TestAdapterManagerLifecycle:
    """Tests for attach/detach refcount semantics."""

    async def test_attach_creates_adapter(self, manager: AdapterManager):
        adapter = await manager.attach("alice", "tui-0001", _noop_sink)
        assert adapter.member_id == "human:alice"
        assert adapter.is_initialized
        assert manager.get_adapter("alice") is adapter
        assert [a.username for a in manager.list_online()] == ["alice"]

    async def test_same_username_shares_adapter(self, manager: AdapterManager):
        first = await manager.attach("alice", "tui-0001", _noop_sink)
        second = await manager.attach("alice", "tui-0002", _noop_sink)
        assert first is second
        assert second.session_count == 2

    async def test_last_detach_destroys_adapter(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001", _noop_sink)
        await manager.attach("alice", "tui-0002", _noop_sink)

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

    async def test_attach_creates_persistent_mailbox(
        self, manager: AdapterManager, tmp_path
    ):
        """First attach creates the human's mailbox — connecting once makes
        the human "known", so offline mail waits in their inbox."""
        await manager.attach("alice", "tui-0001", _noop_sink)
        assert (tmp_path / "human-alice" / "mail").is_dir()

    async def test_concurrent_first_attach_shares_one_adapter(
        self, manager: AdapterManager
    ):
        """The attach lock prevents two adapters for one username."""
        first, second = await asyncio.gather(
            manager.attach("alice", "tui-0001", _noop_sink),
            manager.attach("alice", "tui-0002", _noop_sink),
        )
        assert first is second
        assert first.session_count == 2


class TestAdapterManagerPublish:
    """Tests for speaking through adapters (R2 to/cc/subject)."""

    async def test_publish_by_session(self, bus: EventBus, manager: AdapterManager):
        collected = await _collect_mail(bus)
        await manager.attach("alice", "tui-0001", _noop_sink)

        mail = await manager.publish("tui-0001", "hello", to=["谢云舒"])
        await _drain(bus)

        assert mail is not None
        assert mail.from_address == "alice@tlon.internal"
        assert mail.to == ["xie-yunshu@tlon.internal"]
        assert mail.content == "hello"
        assert collected[-1].id == mail.id

    async def test_publish_explicit_to(self, manager: AdapterManager):
        """R2: explicit to resolves names, ids and full addresses."""
        await manager.attach("alice", "tui-0001", _noop_sink)

        mail = await manager.publish(
            "tui-0001", "hi", to=["谢云舒", "xie-yunshu@tlon.internal"]
        )

        assert mail is not None
        # Name and full-address forms dedupe to one recipient
        assert mail.to == ["xie-yunshu@tlon.internal"]

    async def test_publish_cc_and_subject(self, manager: AdapterManager):
        """R2: cc resolves like to; subject is passed through."""
        await manager.attach("alice", "tui-0001", _noop_sink)
        await manager.attach("friend", "tui-0002", _noop_sink)

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
        await manager.attach("alice", "tui-0001", _noop_sink)
        await manager.attach("friend", "tui-0002", _noop_sink)

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
        await manager.attach("alice", "tui-0001", _noop_sink)

        mail = await manager.publish("tui-0001", "hi", to=["xie-yunshu"])

        assert mail is not None
        assert mail.in_reply_to is None
        assert mail.thread_id == mail.id

    async def test_publish_to_and_cc_both_addressed(
        self, manager: AdapterManager
    ):
        """R2: to and cc resolve independently and both count."""
        await manager.attach("alice", "tui-0001", _noop_sink)
        await manager.attach("friend", "tui-0002", _noop_sink)

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
        await manager.attach("alice", "tui-0001", _noop_sink)

        mail = await manager.publish("tui-0001", "fyi", cc=["xie-yunshu"])

        assert mail is not None
        assert mail.to == []
        assert mail.cc == ["xie-yunshu@tlon.internal"]

    async def test_publish_to_offline_known_human(
        self, manager: AdapterManager, tmp_path
    ):
        """A mail to a known-but-offline human (persistent mailbox, no
        adapter) is addressed, not dropped — the copy waits in their
        inbox on the delivery side (docs/design/mail.md)."""
        await manager.attach("alice", "tui-0001", _noop_sink)
        TlonPaths(home=tmp_path).member_mail_dir("human:friend").mkdir(
            parents=True, exist_ok=True
        )

        mail = await manager.publish("tui-0001", "hi", to=["friend"])

        assert mail is not None
        assert mail.to == ["friend@tlon.internal"]

    async def test_publish_zero_recipients_raises(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001", _noop_sink)

        with pytest.raises(ValueError, match="收件人"):
            await manager.publish("tui-0001", "hello, anyone?")

    async def test_publish_unresolvable_recipients_raise(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001", _noop_sink)

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
        await manager.attach("alice", "tui-0001", _noop_sink)

        assert manager.resolve_address("alice") == "alice@tlon.internal"
        assert manager.resolve_address("human:alice") == "alice@tlon.internal"
        assert manager.resolve_address("offline-user") is None

    async def test_offline_human_with_mailbox(
        self, manager: AdapterManager, tmp_path
    ):
        """A known-but-offline human (persistent mailbox, no adapter)
        resolves — same rule as MailDelivery, the mail waits in their
        inbox (docs/design/mail.md)."""
        TlonPaths(home=tmp_path).member_mail_dir("human:friend").mkdir(
            parents=True, exist_ok=True
        )

        assert manager.resolve_address("friend") == "friend@tlon.internal"
        assert manager.resolve_address("human:friend") == "friend@tlon.internal"

    async def test_offline_human_without_mailbox_unresolved(
        self, manager: AdapterManager
    ):
        """An offline human with no persistent mailbox stays unknown."""
        assert manager.resolve_address("ghost") is None
        assert manager.resolve_address("human:ghost") is None

    async def test_username_for(self, manager: AdapterManager):
        await manager.attach("alice", "tui-0001", _noop_sink)

        assert manager.username_for("tui-0001") == "alice"
        assert manager.username_for("tui-9999") is None


class TestHumanMailDelivery:
    """Tests for single-track mail delivery through the adapter.

    The HumanAdapter subscribes to MAIL_DELIVERED and fans out copies
    addressed to its own member id ("human:<username>") to its attached
    session sinks; the NotificationService no longer pushes mail to chat
    sessions.
    """

    async def test_delivered_mail_fans_out_to_all_sessions(
        self, bus: EventBus, manager: AdapterManager
    ):
        """Both sessions of the same username receive the delivered mail."""
        first: list[Event] = []
        second: list[Event] = []
        await manager.attach("alice", "tui-0001", _collecting_sink(first))
        await manager.attach("alice", "tui-0002", _collecting_sink(second))

        mail = _make_mail(to=["alice@tlon.internal"])
        await _deliver_mail(bus, mail, "human:alice")

        assert [e.payload["mail"].id for e in first] == [mail.id]
        assert [e.payload["mail"].id for e in second] == [mail.id]

    async def test_mail_for_other_recipient_ignored(
        self, bus: EventBus, manager: AdapterManager
    ):
        """A copy addressed to another member is not pushed."""
        collected: list[Event] = []
        await manager.attach("alice", "tui-0001", _collecting_sink(collected))

        mail = _make_mail(to=["friend@tlon.internal"])
        await _deliver_mail(bus, mail, "human:friend")

        assert collected == []

    async def test_no_sessions_is_silent(self, bus: EventBus):
        """An adapter without sessions swallows its mail without error."""
        adapter = HumanAdapter("alice", bus, lambda name: None)
        await adapter.initialize()

        mail = _make_mail(to=["alice@tlon.internal"])
        await _deliver_mail(bus, mail, "human:alice")  # must not raise

        await adapter.shutdown()

    async def test_sink_error_isolated(
        self, bus: EventBus, manager: AdapterManager
    ):
        """A failing sink does not prevent delivery to the other sinks."""
        collected: list[Event] = []

        def broken_sink(event: Event) -> None:
            raise RuntimeError("ws gone")

        await manager.attach("alice", "tui-0001", broken_sink)
        await manager.attach("alice", "tui-0002", _collecting_sink(collected))

        mail = _make_mail(to=["alice@tlon.internal"])
        await _deliver_mail(bus, mail, "human:alice")

        assert [e.payload["mail"].id for e in collected] == [mail.id]

    async def test_missing_recipient_ignored(
        self, bus: EventBus, manager: AdapterManager
    ):
        """A malformed payload (no recipient) is ignored."""
        collected: list[Event] = []
        await manager.attach("alice", "tui-0001", _collecting_sink(collected))

        await bus.publish(
            Event(
                type=EventType.MAIL_DELIVERED,
                payload={"not": "delivery"},
                source="mail-delivery",
            )
        )
        await _drain(bus)

        assert collected == []
