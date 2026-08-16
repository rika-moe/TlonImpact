"""Tests for the shared RecipientResolver (organization/mail/resolver.py).

Issue : recipient resolution converged to a single authority — the
adapter side (speak: raw name -> address) and the delivery side
(address -> member id) delegate to the same RecipientResolver instance,
so the "known human" rule (online OR persistent mailbox) exists once.

Covers:
- AdapterManager and MailDelivery share one resolver instance
- display-name resolution through the shared resolver
- speak-side resolvable <=> delivery-side deliverable (round trip),
  for online humans and known-but-offline humans
- agents win over humans on a colliding local part
"""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.base.config import AgentConfig
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.delivery import MailDelivery
from tlon.organization.mail.resolver import RecipientResolver
from tlon.organization.messaging.human_adapter import AdapterManager


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
async def manager(
    provider: MockAgentInfoProvider, tmp_path
) -> AsyncGenerator[AdapterManager]:
    """Provide an initialized AdapterManager (isolated tlon home)."""
    bus = EventBus()
    await bus.initialize()
    mgr = AdapterManager(
        event_bus=bus,
        agent_info_provider=provider,
        tlon_paths=TlonPaths(home=tmp_path),
    )
    await mgr.initialize()
    yield mgr
    await mgr.shutdown()
    await bus.shutdown()


def _make_delivery(
    manager: AdapterManager, provider: MockAgentInfoProvider, tmp_path
) -> MailDelivery:
    bus = MagicMock()
    bus.publish = AsyncMock()
    bus.subscribe = MagicMock(side_effect=lambda *_args, **_kwargs: "sub-x")
    return MailDelivery(
        event_bus=bus,
        agent_info_provider=provider,
        adapter_manager=manager,
        tlon_paths=TlonPaths(home=tmp_path),
    )


class TestSharedResolver:
    """Both resolution sides delegate to one RecipientResolver instance."""

    def test_delivery_shares_the_adapter_manager_resolver(
        self,
        manager: AdapterManager,
        provider: MockAgentInfoProvider,
        tmp_path,
    ):
        delivery = _make_delivery(manager, provider, tmp_path)
        assert isinstance(manager.recipient_resolver, RecipientResolver)
        assert delivery._resolver is manager.recipient_resolver

    async def test_display_name_resolution_via_shared_resolver(
        self, manager: AdapterManager
    ):
        resolver = manager.recipient_resolver
        assert resolver.resolve_address("谢云舒") == "xie-yunshu@tlon.internal"
        assert resolver.resolve_member("xie-yunshu@tlon.internal") == "xie-yunshu"

    async def test_online_human_round_trip(
        self, manager: AdapterManager, tmp_path
    ):
        await manager.attach("alice", "sess-1", lambda event: None)
        resolver = manager.recipient_resolver
        address = resolver.resolve_address("alice")
        assert address == "alice@tlon.internal"
        assert resolver.resolve_member(address) == "human:alice"

    async def test_offline_human_with_mailbox_round_trip(
        self, manager: AdapterManager, tmp_path
    ):
        # Known-but-offline: a persistent mailbox exists, no live adapter
        manager._paths.member_mail_dir("human:friend")
        resolver = manager.recipient_resolver
        address = resolver.resolve_address("friend")
        assert address == "friend@tlon.internal"
        assert resolver.resolve_member(address) == "human:friend"

    async def test_unknown_human_unresolvable_on_both_sides(
        self, manager: AdapterManager
    ):
        resolver = manager.recipient_resolver
        assert resolver.resolve_address("ghost") is None
        assert resolver.resolve_member("ghost@tlon.internal") is None

    async def test_agent_wins_over_colliding_human(
        self, provider: MockAgentInfoProvider, tmp_path
    ):
        provider._configs["alice"] = AgentConfig(id="alice", name="撞名")
        bus = EventBus()
        await bus.initialize()
        mgr = AdapterManager(
            event_bus=bus,
            agent_info_provider=provider,
            tlon_paths=TlonPaths(home=tmp_path),
        )
        await mgr.attach("alice", "sess-1", lambda event: None)
        resolver = mgr.recipient_resolver
        # Speak side: agent id direct hit precedes the human lookup
        assert resolver.resolve_address("alice") == "alice@tlon.internal"
        # Delivery side: the agent member id wins the colliding local part
        assert resolver.resolve_member("alice@tlon.internal") == "alice"
        await mgr.shutdown()
        await bus.shutdown()

    async def test_ambiguous_display_name_is_refused(
        self, provider: MockAgentInfoProvider
    ):
        """Two agents sharing a display name must not resolve silently to
        the first one — the addressing surface is the name (ADR 0006), so
        a collision refuses explicitly (None), pushing the sender to the
        stable agent id."""
        provider._configs["dupe-1"] = AgentConfig(id="dupe-1", name="谢云舒")
        resolver = RecipientResolver(
            agent_info_provider=provider,
            is_human_online=lambda _username: False,
        )

        assert resolver.resolve_address("谢云舒") is None
        # The stable id still resolves (the escape hatch)
        assert resolver.resolve_address("dupe-1") == "dupe-1@tlon.internal"
        assert resolver.resolve_address("xie-yunshu") == "xie-yunshu@tlon.internal"

    async def test_unique_display_name_still_resolves(
        self, provider: MockAgentInfoProvider
    ):
        resolver = RecipientResolver(
            agent_info_provider=provider,
            is_human_online=lambda _username: False,
        )
        assert resolver.resolve_address("谢云舒") == "xie-yunshu@tlon.internal"
