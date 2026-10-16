"""Explicit post-office fixtures for runtime unit tests."""

from pathlib import Path

from tlon.base.config import AgentConfig
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.post_office import PostOffice
from tlon.organization.mail.resolver import RecipientResolver


class TestMembers:
    def __init__(self, ids: list[str]) -> None:
        self.configs = {i: AgentConfig(id=i, name=i) for i in ids}

    def get_config(self, member: str) -> AgentConfig | None:
        return self.configs.get(member)

    def get_all_ids(self) -> set[str]:
        return set(self.configs)

    async def reload_agent(self, member: str) -> bool:
        return False

    def delete_agent(self, member: str) -> None:
        self.configs.pop(member, None)


def make_office(bus: EventBus, home: Path, ids: list[str] | None = None) -> PostOffice:
    paths = TlonPaths(home=home)
    members = TestMembers(
        ids or ["agent1", "user", "sink", "relay", "worker", "alice", "bob", "a", "b", "adam"]
    )
    return PostOffice(bus, RecipientResolver(members, paths), paths)
