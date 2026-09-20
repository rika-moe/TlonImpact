"""Tests for the mail delivery component (organization/mail/delivery.py).

Covers:
- delivery to valid agent / online-human recipients (inbox + sent files,
  one MAIL_DELIVERED per recipient)
- all@tlon.internal expansion (all registered agents + online humans)
- unknown recipients collected into a postmaster bounce to the sender
- senders without a mailbox (postmaster) skip the sent copy
- the guardian address (adam@tlon.internal, issue ): deliverable via
  the guardian-aware provider, excluded from the broadcast expansion
- postmaster conventions (issue ): mail *to* postmaster is dropped
  without a bounce; mail *from* postmaster is never bounced
"""

import tempfile
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.base.config import AgentConfig
from tlon.base.events.event import Event, EventType
from tlon.base.paths import TlonPaths
from tlon.organization.mail.delivery import MailDelivery
from tlon.organization.mail.gateways import ExternalGatewayRegistry
from tlon.organization.mail.guardian_provider import GuardianAgentInfoProvider
from tlon.organization.mail.mail import (
    ALL_ADDRESS,
    POSTMASTER_ADDRESS,
    Mail,
    address_for_member,
)
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.messaging.human_adapter import AdapterManager


class MockAgentInfoProvider:
    """Mock AgentInfoProvider for testing."""

    def __init__(self, configs: dict[str, AgentConfig]):
        self._configs = configs

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)

    def get_all_ids(self) -> set[str]:
        return set(self._configs.keys())


def _make_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock()
    bus.subscribe = MagicMock(side_effect=lambda *_args, **_kwargs: "sub-x")
    bus.unsubscribe = MagicMock()
    return bus


@pytest.fixture
def temp_paths():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield TlonPaths(home=tmpdir)


def _make_delivery(
    temp_paths: TlonPaths,
    configs: dict[str, AgentConfig] | None = None,
    online_humans: list[str] | None = None,
    external_owner: Callable[[str], str | None] | None = None,
    guardian: bool = False,
):
    bus = _make_bus()
    provider: Any = MockAgentInfoProvider(configs or {})
    if guardian:
        # The production assembly shape (issue ): ServerApp wraps the
        # ConfigService once and feeds both resolution sides.
        provider = GuardianAgentInfoProvider(provider)
    adapter_manager = AdapterManager(
        event_bus=bus, agent_info_provider=provider, tlon_paths=temp_paths,
        external_owner=external_owner,
    )
    for username in online_humans or []:
        adapter_manager._adapters[username] = MagicMock(
            member_id=f"human:{username}",
            username=username,
            address=address_for_member(f"human:{username}"),
            session_count=1,
        )
    delivery = MailDelivery(
        event_bus=bus,
        agent_info_provider=provider,
        adapter_manager=adapter_manager,
        tlon_paths=temp_paths,
    )
    return delivery, bus


def _mail_event(mail: Mail) -> Event:
    return Event(type=EventType.MAIL_NEW, payload=mail, source="test")


def _delivered(bus: MagicMock) -> list[Event]:
    return [
        call.args[0]
        for call in bus.publish.await_args_list
        if call.args[0].type == EventType.MAIL_DELIVERED
    ]


class TestValidDelivery:
    @pytest.mark.asyncio
    async def test_agent_recipient_gets_inbox_and_event(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="alice@tlon.internal",
            to=["a-001@tlon.internal"],
            content="你好",
        )
        await delivery._on_mail_new(_mail_event(mail))

        # One MAIL_DELIVERED, targeted at the agent member id
        events = _delivered(bus)
        assert len(events) == 1
        assert events[0].payload["recipient"] == "a-001"
        assert events[0].payload["mail"].id == mail.id

        # Inbox persisted
        inbox = Mailbox(temp_paths.member_mail_dir("a-001"))
        unread = await inbox.unread()
        assert [m.id for m in unread] == [mail.id]

    @pytest.mark.asyncio
    async def test_sender_sent_copy_for_known_sender(self, temp_paths):
        configs = {
            "a-001": AgentConfig(id="a-001", name="甲"),
            "a-002": AgentConfig(id="a-002", name="乙"),
        }
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=["a-002@tlon.internal"],
            content="汇报",
        )
        await delivery._on_mail_new(_mail_event(mail))

        sent = Mailbox(temp_paths.member_mail_dir("a-001"))
        recent = await sent.recent()
        assert [m.id for m in recent] == [mail.id]

    @pytest.mark.asyncio
    async def test_human_recipient_resolves_to_human_member_id(self, temp_paths):
        delivery, bus = _make_delivery(temp_paths, online_humans=["alice"])
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=["alice@tlon.internal"],
            content="hi",
        )
        await delivery._on_mail_new(_mail_event(mail))

        events = _delivered(bus)
        assert len(events) == 1
        assert events[0].payload["recipient"] == "human:alice"

        inbox = Mailbox(temp_paths.member_mail_dir("human:alice"))
        assert [m.id for m in await inbox.unread()] == [mail.id]

    @pytest.mark.asyncio
    async def test_cc_recipients_also_get_delivery(self, temp_paths):
        configs = {
            "a-001": AgentConfig(id="a-001", name="甲"),
            "a-002": AgentConfig(id="a-002", name="乙"),
        }
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=["a-002@tlon.internal"],
            cc=["a-001@tlon.internal"],
            content="抄送",
        )
        await delivery._on_mail_new(_mail_event(mail))

        recipients = {e.payload["recipient"] for e in _delivered(bus)}
        assert recipients == {"a-001", "a-002"}


class TestAllExpansion:
    @pytest.mark.asyncio
    async def test_all_address_expands_to_every_member(self, temp_paths):
        configs = {
            "a-001": AgentConfig(id="a-001", name="甲"),
            "a-002": AgentConfig(id="a-002", name="乙"),
        }
        delivery, bus = _make_delivery(
            temp_paths, configs, online_humans=["alice"]
        )
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=[ALL_ADDRESS],
            content="全员通知",
        )
        await delivery._on_mail_new(_mail_event(mail))

        recipients = {e.payload["recipient"] for e in _delivered(bus)}
        assert recipients == {"a-001", "a-002", "human:alice"}

        # Every member's inbox got the mail
        for member_id in ("a-001", "a-002", "human:alice"):
            inbox = Mailbox(temp_paths.member_mail_dir(member_id))
            assert [m.id for m in await inbox.unread()] == [mail.id]

    @pytest.mark.asyncio
    async def test_all_mixed_with_direct_recipient_dedupes(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="alice@tlon.internal",
            to=[ALL_ADDRESS, "a-001@tlon.internal"],
            content="去重",
        )
        await delivery._on_mail_new(_mail_event(mail))

        recipients = [e.payload["recipient"] for e in _delivered(bus)]
        assert recipients == ["a-001"]


class TestBounce:
    @pytest.mark.asyncio
    async def test_unknown_recipient_bounces_to_sender(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=["ghost@tlon.internal"],
            content="有人吗",
        )
        await delivery._on_mail_new(_mail_event(mail))

        # Exactly one MAIL_DELIVERED: the bounce, to the sender
        events = _delivered(bus)
        assert len(events) == 1
        assert events[0].payload["recipient"] == "a-001"
        bounce: Mail = events[0].payload["mail"]
        assert bounce.from_address == POSTMASTER_ADDRESS
        assert "ghost@tlon.internal" in bounce.content
        assert bounce.in_reply_to == mail.id
        assert bounce.thread_id == mail.thread_id

        # Bounce landed in the sender's inbox, original mail did not
        inbox = Mailbox(temp_paths.member_mail_dir("a-001"))
        assert [m.id for m in await inbox.unread()] == [bounce.id]

    @pytest.mark.asyncio
    async def test_mixed_valid_and_unknown_delivers_and_bounces(self, temp_paths):
        configs = {
            "a-001": AgentConfig(id="a-001", name="甲"),
            "a-002": AgentConfig(id="a-002", name="乙"),
        }
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=["a-002@tlon.internal", "ghost@tlon.internal"],
            content="混合",
        )
        await delivery._on_mail_new(_mail_event(mail))

        events = _delivered(bus)
        by_recipient = {e.payload["recipient"]: e.payload["mail"] for e in events}
        assert by_recipient["a-002"].id == mail.id
        assert by_recipient["a-001"].from_address == POSTMASTER_ADDRESS

    @pytest.mark.asyncio
    async def test_unknown_sender_gets_no_sent_copy_and_no_bounce(self, temp_paths):
        delivery, bus = _make_delivery(temp_paths)
        await delivery.initialize()

        mail = Mail.create(
            from_address="nobody@tlon.internal",
            to=["ghost@tlon.internal"],
            content="无处可去",
        )
        await delivery._on_mail_new(_mail_event(mail))

        assert _delivered(bus) == []
        assert not (temp_paths.tlon_home() / "nobody").exists()


class TestExternalSender:
    """External senders (real-world addresses, issue ): an external
    address resolves to no member, so the mail delivers to recipients
    normally but the sender gets no sent copy and no bounce."""

    @pytest.mark.asyncio
    async def test_external_mail_delivers_to_member_inbox(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="notifications@github.com",
            to=["a-001@tlon.internal"],
            content="CI 失败：main 分支构建红了",
        )
        await delivery._on_mail_new(_mail_event(mail))

        events = _delivered(bus)
        assert len(events) == 1
        assert events[0].payload["recipient"] == "a-001"
        assert events[0].payload["mail"].id == mail.id

        inbox = Mailbox(temp_paths.member_mail_dir("a-001"))
        assert [m.id for m in await inbox.unread()] == [mail.id]

    @pytest.mark.asyncio
    async def test_external_sender_gets_no_sent_copy(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="notifications@github.com",
            to=["a-001@tlon.internal"],
            content="有新 issue",
        )
        await delivery._on_mail_new(_mail_event(mail))

        # No mailbox directory is created for the external sender
        assert not (temp_paths.tlon_home() / "notifications").exists()

    @pytest.mark.asyncio
    async def test_external_sender_unknown_recipient_no_bounce(self, temp_paths):
        """An external sender has no mailbox: the bounce is dropped
        safely (logged), never delivered anywhere."""
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="alerts@monitoring.example.org",
            to=["ghost@tlon.internal"],
            content="告警：磁盘 95%",
        )
        await delivery._on_mail_new(_mail_event(mail))

        assert _delivered(bus) == []  # no delivery, no bounce
        assert not (temp_paths.tlon_home() / "alerts").exists()

    @pytest.mark.asyncio
    async def test_external_sender_mixed_recipients_delivers_valid(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="notifications@github.com",
            to=["a-001@tlon.internal", "ghost@tlon.internal"],
            content="混合收件人",
        )
        await delivery._on_mail_new(_mail_event(mail))

        events = _delivered(bus)
        assert len(events) == 1  # valid recipient only; no bounce
        assert events[0].payload["recipient"] == "a-001"
        assert events[0].payload["mail"].id == mail.id


class TestExternalGatewayRecipient:
    """Egress direction (issue ): an external recipient claimed by a
    gateway resolves to the gateway member id and delivers normally —
    the gateway mailbox is the outbound audit trail. Unclaimed external
    recipients keep bouncing."""

    EXTERNAL = "notifications@github.com"

    @pytest.mark.asyncio
    async def test_claimed_external_recipient_delivers_to_gateway(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        registry = ExternalGatewayRegistry()
        registry.claim(self.EXTERNAL, "gateway:github")
        delivery, bus = _make_delivery(
            temp_paths, configs, external_owner=registry.owner_of
        )
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=[self.EXTERNAL],
            content="收到，我来处理",
        )
        await delivery._on_mail_new(_mail_event(mail))

        # Exactly one MAIL_DELIVERED: to the gateway member, no bounce
        events = _delivered(bus)
        assert len(events) == 1
        assert events[0].payload["recipient"] == "gateway:github"
        assert events[0].payload["mail"].id == mail.id

        # The gateway mailbox holds the outbound copy (audit trail)
        inbox = Mailbox(temp_paths.member_mail_dir("gateway:github"))
        assert [m.id for m in await inbox.unread()] == [mail.id]

    @pytest.mark.asyncio
    async def test_unclaimed_external_recipient_still_bounces(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        registry = ExternalGatewayRegistry()
        registry.claim(self.EXTERNAL, "gateway:github")
        delivery, bus = _make_delivery(
            temp_paths, configs, external_owner=registry.owner_of
        )
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=["typo@github.com"],  # not the claimed address
            content="发错域了",
        )
        await delivery._on_mail_new(_mail_event(mail))

        # Exactly one MAIL_DELIVERED: the bounce, to the sender
        events = _delivered(bus)
        assert len(events) == 1
        assert events[0].payload["recipient"] == "a-001"
        bounce: Mail = events[0].payload["mail"]
        assert bounce.from_address == POSTMASTER_ADDRESS
        assert "typo@github.com" in bounce.content


class TestOfflineHumans:
    """Mail to a known-but-offline human waits in their inbox (mail.md 人类侧).

    A human is "known" once their persistent mailbox exists; only an
    address with no mailbox at all counts as unknown and bounces.
    """

    @pytest.mark.asyncio
    async def test_offline_known_human_receives_mail_no_bounce(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        delivery, bus = _make_delivery(temp_paths, configs)  # nobody online
        await delivery.initialize()
        # The human has a persistent mailbox (connected at least once)
        temp_paths.member_mail_dir("human:alice")

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=["alice@tlon.internal"],
            content="离线也等你",
        )
        await delivery._on_mail_new(_mail_event(mail))

        # Delivered to the human's inbox — exactly one event, no bounce
        events = _delivered(bus)
        assert len(events) == 1
        assert events[0].payload["recipient"] == "human:alice"
        assert events[0].payload["mail"].id == mail.id

        inbox = Mailbox(temp_paths.member_mail_dir("human:alice"))
        assert [m.id for m in await inbox.unread()] == [mail.id]

    @pytest.mark.asyncio
    async def test_never_seen_human_still_bounces(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=["ghost@tlon.internal"],
            content="有人吗",
        )
        await delivery._on_mail_new(_mail_event(mail))

        events = _delivered(bus)
        assert len(events) == 1  # only the bounce, to the sender
        assert events[0].payload["recipient"] == "a-001"
        assert events[0].payload["mail"].from_address == POSTMASTER_ADDRESS

    @pytest.mark.asyncio
    async def test_all_includes_offline_known_human(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        delivery, bus = _make_delivery(temp_paths, configs)  # nobody online
        await delivery.initialize()
        temp_paths.member_mail_dir("human:alice")  # known, offline

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=[ALL_ADDRESS],
            content="全员通知",
        )
        await delivery._on_mail_new(_mail_event(mail))

        recipients = {e.payload["recipient"] for e in _delivered(bus)}
        assert recipients == {"a-001", "human:alice"}

        inbox = Mailbox(temp_paths.member_mail_dir("human:alice"))
        assert [m.id for m in await inbox.unread()] == [mail.id]


class TestIdempotentDelivery:
    """A republished MAIL_NEW must not duplicate inbox/sent copies or events."""

    @pytest.mark.asyncio
    async def test_republished_mail_is_delivered_once(self, temp_paths):
        configs = {
            "a-001": AgentConfig(id="a-001", name="甲"),
            "a-002": AgentConfig(id="a-002", name="乙"),
        }
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=["a-002@tlon.internal"],
            content="只到一次",
        )
        await delivery._on_mail_new(_mail_event(mail))
        await delivery._on_mail_new(_mail_event(mail))  # replay

        assert len(_delivered(bus)) == 1
        inbox = Mailbox(temp_paths.member_mail_dir("a-002"))
        assert [m.id for m in await inbox.unread()] == [mail.id]
        sent = Mailbox(temp_paths.member_mail_dir("a-001"))
        assert [m.id for m in await sent.recent()] == [mail.id]

    @pytest.mark.asyncio
    async def test_republished_unknown_recipient_bounces_once(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=["ghost@tlon.internal"],
            content="有人吗",
        )
        await delivery._on_mail_new(_mail_event(mail))
        await delivery._on_mail_new(_mail_event(mail))  # replay

        assert len(_delivered(bus)) == 1  # one bounce only
        inbox = Mailbox(temp_paths.member_mail_dir("a-001"))
        unread = await inbox.unread()
        assert len(unread) == 1
        assert unread[0].from_address == POSTMASTER_ADDRESS


class TestPayloadGuard:
    @pytest.mark.asyncio
    async def test_non_mail_payload_is_ignored(self, temp_paths):
        delivery, bus = _make_delivery(temp_paths)
        await delivery.initialize()

        await delivery._on_mail_new(
            Event(type=EventType.MAIL_NEW, payload={"not": "mail"}, source="x")
        )

        assert _delivered(bus) == []


class TestMailboxCacheLRU:
    """The on-demand Mailbox cache is a bounded LRU (_MAILBOXES_CAP)."""

    def test_evicts_oldest_over_cap(self, temp_paths, monkeypatch):
        monkeypatch.setattr("tlon.organization.mail.delivery._MAILBOXES_CAP", 2)
        delivery, _bus = _make_delivery(temp_paths)

        a = delivery._mailbox_for("a")
        delivery._mailbox_for("b")
        delivery._mailbox_for("c")  # overflow: "a" evicted
        assert set(delivery._mailboxes) == {"b", "c"}

        # Eviction is safe: the rebuilt instance is a new stateless
        # handle, but mutual exclusion comes from the path-shared lock
        # in mailbox._LOCKS, so it acquires the very same lock.
        rebuilt = delivery._mailbox_for("a")
        assert rebuilt is not a
        assert rebuilt._lock is a._lock

    def test_hit_refreshes_recency(self, temp_paths, monkeypatch):
        monkeypatch.setattr("tlon.organization.mail.delivery._MAILBOXES_CAP", 2)
        delivery, _bus = _make_delivery(temp_paths)

        a = delivery._mailbox_for("a")
        delivery._mailbox_for("b")
        assert delivery._mailbox_for("a") is a  # hit: "a" most recent now
        delivery._mailbox_for("c")  # overflow: "b" evicted
        assert set(delivery._mailboxes) == {"a", "c"}


class TestEndorsedEvent:
    """A delivered mail carrying an `endorsing` reference publishes one
    MAIL_ENDORSED reputation fact (issue )."""

    @pytest.mark.asyncio
    async def test_endorsement_publishes_mail_endorsed(self, temp_paths):
        configs = {
            "a-001": AgentConfig(id="a-001", name="甲"),
            "a-002": AgentConfig(id="a-002", name="乙"),
        }
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        # The endorsed mail sits in the endorser's mailbox (the executor
        # guarantees this for agent senders).
        endorsed = Mail.create(
            from_address="a-002@tlon.internal",
            to=["a-001@tlon.internal"],
            content="有用的分析",
            subject="分析报告",
        )
        await Mailbox(temp_paths.member_mail_dir("a-001")).append_inbox(endorsed)

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=["a-002@tlon.internal"],
            content="这封信帮到了我，谢谢！",
            endorsing=endorsed.id,
            thread_id="t-9",
        )
        await delivery._on_mail_new(_mail_event(mail))

        endorsed_events = [
            call.args[0]
            for call in bus.publish.await_args_list
            if call.args[0].type == EventType.MAIL_ENDORSED
        ]
        assert len(endorsed_events) == 1
        payload = endorsed_events[0].payload
        assert payload["endorser"] == "a-001"
        assert payload["mail_id"] == endorsed.id
        assert payload["author"] == "a-002@tlon.internal"
        assert payload["thread_id"] == "t-9"

    @pytest.mark.asyncio
    async def test_plain_mail_publishes_no_endorsement(self, temp_paths):
        configs = {
            "a-001": AgentConfig(id="a-001", name="甲"),
            "a-002": AgentConfig(id="a-002", name="乙"),
        }
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=["a-002@tlon.internal"],
            content="普通来信",
        )
        await delivery._on_mail_new(_mail_event(mail))

        assert not [
            call
            for call in bus.publish.await_args_list
            if call.args[0].type == EventType.MAIL_ENDORSED
        ]

    @pytest.mark.asyncio
    async def test_republish_does_not_emit_endorsement_twice(self, temp_paths):
        configs = {
            "a-001": AgentConfig(id="a-001", name="甲"),
            "a-002": AgentConfig(id="a-002", name="乙"),
        }
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=["a-002@tlon.internal"],
            content="致谢",
            endorsing="m-x",
        )
        await delivery._on_mail_new(_mail_event(mail))
        await delivery._on_mail_new(_mail_event(mail))  # replay

        endorsed_events = [
            call.args[0]
            for call in bus.publish.await_args_list
            if call.args[0].type == EventType.MAIL_ENDORSED
        ]
        assert len(endorsed_events) == 1
        # The endorsed mail is nowhere on disk: author degrades to None.
        assert endorsed_events[0].payload["author"] is None


class TestGuardianDelivery:
    """Issue : adam@tlon.internal is deliverable through the
    guardian-aware provider, yet the guardian stays out of the broadcast
    expansion (get_all_ids delegates unchanged)."""

    @pytest.mark.asyncio
    async def test_adam_recipient_gets_inbox_and_event(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        delivery, bus = _make_delivery(temp_paths, configs, guardian=True)
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=["adam@tlon.internal"],
            content="守护者，提案请你过目",
        )
        await delivery._on_mail_new(_mail_event(mail))

        events = _delivered(bus)
        assert len(events) == 1
        assert events[0].payload["recipient"] == "adam"
        assert events[0].payload["mail"].id == mail.id

        # Delivery creates the guardian mailbox on demand
        inbox = Mailbox(temp_paths.member_mail_dir("adam"))
        assert [m.id for m in await inbox.unread()] == [mail.id]

    @pytest.mark.asyncio
    async def test_broadcast_excludes_adam(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        delivery, bus = _make_delivery(temp_paths, configs, guardian=True)
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=[ALL_ADDRESS],
            content="全员通知",
        )
        await delivery._on_mail_new(_mail_event(mail))

        recipients = {e.payload["recipient"] for e in _delivered(bus)}
        assert recipients == {"a-001"}
        # No guardian mailbox is created by a broadcast
        assert not (temp_paths.tlon_home() / "adam").exists()

    @pytest.mark.asyncio
    async def test_adam_unresolvable_without_the_guardian_wrap(self, temp_paths):
        """The plain provider (no wrapper) keeps the old behavior: mail to
        adam bounces — the wrapper is what makes the guardian reachable."""
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=["adam@tlon.internal"],
            content="有人吗",
        )
        await delivery._on_mail_new(_mail_event(mail))

        events = _delivered(bus)
        assert len(events) == 1  # only the bounce, to the sender
        assert events[0].payload["recipient"] == "a-001"
        assert events[0].payload["mail"].from_address == POSTMASTER_ADDRESS


class TestPostmasterConventions:
    """Issue : bounces must never loop — mail *to* postmaster is
    silently dropped, and a mail *from* postmaster is never bounced."""

    @pytest.mark.asyncio
    async def test_mail_to_postmaster_is_dropped_without_bounce(self, temp_paths):
        configs = {"a-001": AgentConfig(id="a-001", name="甲")}
        delivery, bus = _make_delivery(temp_paths, configs)
        await delivery.initialize()

        mail = Mail.create(
            from_address="a-001@tlon.internal",
            to=[POSTMASTER_ADDRESS],
            content="回复退信",
        )
        await delivery._on_mail_new(_mail_event(mail))

        assert _delivered(bus) == []  # no delivery, no bounce
        assert not (temp_paths.tlon_home() / "postmaster").exists()
        # The sender's own sent copy is still recorded
        sent = Mailbox(temp_paths.member_mail_dir("a-001"))
        assert [m.id for m in await sent.recent()] == [mail.id]

    @pytest.mark.asyncio
    async def test_postmaster_sender_unknown_recipient_is_not_bounced(
        self, temp_paths
    ):
        """A system mail (e.g. a cron tick) to an unknown recipient used to
        bounce to postmaster — the double guard keeps it silent."""
        delivery, bus = _make_delivery(temp_paths)
        await delivery.initialize()

        mail = Mail.create(
            from_address=POSTMASTER_ADDRESS,
            to=["ghost@tlon.internal"],
            content="周期任务 tick",
        )
        await delivery._on_mail_new(_mail_event(mail))

        assert _delivered(bus) == []
        assert not (temp_paths.tlon_home() / "postmaster").exists()
