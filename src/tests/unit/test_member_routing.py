"""Unit tests for unified member routing (docs/design/members.md 阶段 B).

Covers:
- MemberDirectory.resolve_mentions: agent ID/name, online human
  username / "human:" form, unresolved kept as-is
- MemberDirectory.triggers_proactive: only online human adapters
- MemberDirectory.list_humans: one MemberInfo per online user
- MessageRouter unified _decide_targets:
  - mentions → directed resolution (agents and humans, one table)
  - no mentions + adapter sender → proactive broadcast
  - agent message without mentions → no targets (阶段 C: "@clients" removed)
  - system message → no targets
"""

from dataclasses import dataclass

import pytest

from tlon.app.server.core.member_directory import MemberDirectory
from tlon.base.events.event_bus import EventBus
from tlon.organization.messaging.human_adapter import AdapterManager
from tlon.organization.messaging.message import Message
from tlon.organization.messaging.router import MessageRouter


@dataclass
class FakeAgentConfig:
    """Minimal agent config for directory tests."""

    id: str
    name: str


class FakeAgentRegistry:
    """Minimal AgentRegistry stand-in (registered agents, sleeping or not)."""

    def __init__(self, configs: list[FakeAgentConfig]):
        self._configs = {c.id: c for c in configs}

    def get_all(self) -> list[str]:
        return list(self._configs)

    def get_config(self, agent_id: str) -> FakeAgentConfig | None:
        return self._configs.get(agent_id)


class FakeAgentInfoProvider:
    """Minimal AgentInfoProvider stand-in for the router."""

    def __init__(self, proactive_ids: set[str], all_ids: list[str]):
        self._proactive = proactive_ids
        self._all_ids = all_ids

    def get_all_ids(self) -> list[str]:
        return self._all_ids

    def is_proactive(self, agent_id: str) -> bool:
        return agent_id in self._proactive


class FakeMessageStore:
    """Minimal MessageStore stand-in (router only calls append)."""

    async def append(self, message: Message) -> None:
        pass


@pytest.fixture
def agent_configs() -> list[FakeAgentConfig]:
    """Two agents: one proactive, one not."""
    return [
        FakeAgentConfig(id="xie-yunshu", name="谢云舒"),
        FakeAgentConfig(id="li-bai", name="李白"),
    ]


@pytest.fixture
def directory(agent_configs: list[FakeAgentConfig]) -> tuple[MemberDirectory, AdapterManager]:
    """MemberDirectory over fake registry + real AdapterManager."""
    bus = EventBus()
    manager = AdapterManager(event_bus=bus)
    directory = MemberDirectory(FakeAgentRegistry(agent_configs), manager)
    return directory, manager


@pytest.fixture
def router(agent_configs: list[FakeAgentConfig]) -> tuple[MessageRouter, AdapterManager]:
    """Router wired to a real MemberDirectory + fake provider."""
    bus = EventBus()
    manager = AdapterManager(event_bus=bus)
    directory = MemberDirectory(FakeAgentRegistry(agent_configs), manager)
    provider = FakeAgentInfoProvider(
        proactive_ids={"xie-yunshu"}, all_ids=[c.id for c in agent_configs]
    )
    router = MessageRouter(
        event_bus=bus,
        message_store=FakeMessageStore(),
        agent_info_provider=provider,
        member_directory=directory,
    )
    return router, manager


def make_human_message(
    content: str = "hi",
    sender_id: str = "human:alice",
    sender_name: str = "alice",
    mentions: list[str] | None = None,
) -> Message:
    """Create a human message as an adapter would publish it."""
    return Message.create(
        sender_id=sender_id,
        sender_name=sender_name,
        sender_type="human",
        content=content,
        mentions=mentions or [],
    )


class TestResolveMentions:
    """Tests for MemberDirectory.resolve_mentions (one table: agents + humans)."""

    async def test_agent_id_and_name(self, directory):
        dir_, _ = directory
        assert dir_.resolve_mentions(["xie-yunshu"]) == {"xie-yunshu"}
        assert dir_.resolve_mentions(["谢云舒"]) == {"xie-yunshu"}

    async def test_human_username_and_prefixed(self, directory):
        dir_, manager = directory
        await manager.attach("alice", "tui-0001")

        assert dir_.resolve_mentions(["alice"]) == {"human:alice"}
        assert dir_.resolve_mentions(["human:alice"]) == {"human:alice"}

    async def test_mixed_and_unresolved(self, directory):
        dir_, manager = directory
        await manager.attach("alice", "tui-0001")

        resolved = dir_.resolve_mentions(["谢云舒", "alice", "nobody"])

        assert resolved == {"xie-yunshu", "human:alice", "nobody"}

    async def test_offline_human_unresolved(self, directory):
        dir_, _ = directory
        assert dir_.resolve_mentions(["human:gone"]) == {"human:gone"}


class TestTriggersProactive:
    """Tests for the proactive-broadcast fuse."""

    async def test_online_human_triggers(self, directory):
        dir_, manager = directory
        await manager.attach("alice", "tui-0001")

        assert dir_.triggers_proactive("human:alice") is True

    async def test_agents_and_unknown_never_trigger(self, directory):
        dir_, _ = directory
        assert dir_.triggers_proactive("xie-yunshu") is False
        assert dir_.triggers_proactive("human") is False
        assert dir_.triggers_proactive("human:offline") is False


class TestListHumans:
    """Tests for human MemberInfo aggregation."""

    async def test_one_entry_per_online_user(self, directory):
        dir_, manager = directory
        await manager.attach("alice", "tui-0001")
        await manager.attach("alice", "tui-0002")
        await manager.attach("bob", "tui-0003")

        humans = dir_.list_humans()

        assert [(h.id, h.name, h.type) for h in humans] == [
            ("human:bob", "bob", "human"),
            ("human:alice", "alice", "human"),
        ]
        alice = next(h for h in humans if h.name == "alice")
        assert alice.metadata["session_count"] == 2

    async def test_no_humans_when_offline(self, directory):
        dir_, _ = directory
        assert dir_.list_humans() == []
        assert dir_.count_all() == 2  # agents only

    async def test_get_member_human(self, directory):
        dir_, manager = directory
        await manager.attach("alice", "tui-0001")

        member = dir_.get_member("human:alice")

        assert member is not None
        assert member.id == "human:alice"
        assert member.name == "alice"
        assert member.type == "human"


class TestUnifiedRouting:
    """Tests for MessageRouter._decide_targets after the 阶段 B unification."""

    async def test_mentions_route_directed_to_agents_and_humans(self, router):
        router_, manager = router
        await manager.attach("alice", "tui-0001")

        message = make_human_message(mentions=["谢云舒", "bob", "alice"])
        targets, mention_targets = router_._decide_targets(message)

        # bob is offline → kept as-is; alice resolves to human:alice
        assert set(targets) == {"xie-yunshu", "bob", "human:alice"}
        assert set(mention_targets) == set(targets)

    async def test_adapter_sender_without_mentions_broadcasts_proactive(self, router):
        router_, manager = router
        await manager.attach("alice", "tui-0001")

        targets, mention_targets = router_._decide_targets(make_human_message())

        assert targets == ["xie-yunshu"]
        assert mention_targets == []

    async def test_unknown_sender_without_mentions_routes_nowhere(self, router):
        router_, _ = router

        # Legacy hardcoded sender_id="human" (no adapter) is not a member:
        # no proactive broadcast anymore
        message = make_human_message(sender_id="human", sender_name="人类")
        targets, _ = router_._decide_targets(message)

        assert targets == []

    async def test_agent_message_without_mentions_no_broadcast(self, router):
        router_, _ = router
        message = Message.create(
            sender_id="li-bai",
            sender_name="李白",
            sender_type="agent",
            content="thinking out loud",
        )

        targets, mention_targets = router_._decide_targets(message)

        # No proactive broadcast (loop fuse); "@clients" marker removed in 阶段 C
        assert targets == []
        assert mention_targets == []

    async def test_agent_message_with_mentions(self, router):
        router_, _ = router
        message = Message.create(
            sender_id="li-bai",
            sender_name="李白",
            sender_type="agent",
            content="@谢云舒 怎么看",
            mentions=["谢云舒"],
        )

        targets, mention_targets = router_._decide_targets(message)

        assert targets == ["xie-yunshu"]
        assert mention_targets == ["xie-yunshu"]

    async def test_system_message_routes_nowhere(self, router):
        router_, _ = router
        message = Message.create(
            sender_id="system",
            sender_name="System",
            sender_type="system",
            content="server restarted",
        )

        targets, mention_targets = router_._decide_targets(message)

        assert targets == []
        assert mention_targets == []
