"""The public member directory follows actual local delivery ownership."""

from pathlib import Path

from tlon.base.config import AgentConfig, AgentInfoProvider
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.guardian_provider import GuardianAgentInfoProvider
from tlon.organization.mail.post_office import PostOffice
from tlon.organization.mail.resolver import RecipientResolver
from tlon.organization.mail.store import MailStore


class DirectoryProvider(AgentInfoProvider):
    def __init__(self) -> None:
        self.configs = {"alice": AgentConfig(id="alice", name="Alice")}

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self.configs.get(agent_id)

    def get_all_ids(self) -> set[str]:
        return set(self.configs)


def test_directory_includes_guardian_and_offline_registered_members(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    provider = GuardianAgentInfoProvider(DirectoryProvider())
    resolver = RecipientResolver(provider, paths)
    store = MailStore(paths.mail_database_file())
    store.register_member("human:rika")
    store.register_member("human:guest")
    office = PostOffice(EventBus(), resolver, paths)

    assert office.list_members() == [
        {"id": "adam", "name": "Adam", "address": "adam@tlon.internal"},
        {"id": "alice", "name": "Alice", "address": "alice@tlon.internal"},
        {"id": "guest", "name": "guest", "address": "guest@tlon.internal"},
        {"id": "rika", "name": "rika", "address": "rika@tlon.internal"},
    ]
    assert provider.get_all_ids() == {"alice"}
    assert not store.member_exists("alice")
    assert not store.member_exists("adam")
    assert not paths.member_mail_dir("adam").exists()


def test_directory_tracks_registration_retirement_and_config_removal(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    provider = DirectoryProvider()
    resolver = RecipientResolver(provider, paths)
    store = MailStore(paths.mail_database_file())
    store.register_member("alice")
    assert [member["id"] for member in resolver.list_members()] == ["alice"]

    store.register_member("human:newcomer")
    assert [member["id"] for member in resolver.list_members()] == ["alice", "newcomer"]
    store.retire("human:newcomer")
    assert [member["id"] for member in resolver.list_members()] == ["alice"]
    provider.configs["bob"] = AgentConfig(id="bob", name="Bob")
    assert [member["id"] for member in resolver.list_members()] == ["alice", "bob"]
    store.retire("bob")
    assert [member["id"] for member in resolver.list_members()] == ["alice"]
    provider.configs.clear()
    assert resolver.list_members() == []


def test_same_localpart_uses_real_owner_and_retirement_does_not_fall_back(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    resolver = RecipientResolver(DirectoryProvider(), paths)
    store = MailStore(paths.mail_database_file())
    store.register_member("human:alice")
    assert resolver.list_members() == [
        {"id": "alice", "name": "Alice", "address": "alice@tlon.internal"}
    ]
    store.retire("alice")
    assert resolver.list_members() == []


def test_retired_human_does_not_hide_active_agent_owner(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    resolver = RecipientResolver(DirectoryProvider(), paths)
    MailStore(paths.mail_database_file()).retire("human:alice")
    assert resolver.list_members() == [
        {"id": "alice", "name": "Alice", "address": "alice@tlon.internal"}
    ]


def test_restart_preserves_directory_and_excludes_nonmembers(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    store = MailStore(paths.mail_database_file())
    for member in [
        "human:rika",
        "human:gone",
        "postmaster",
        "human:postmaster",
        "stale-agent",
        "gateway:github",
        "human:outside@example.com",
    ]:
        store.register_member(member)
    store.retire("human:gone")
    provider = DirectoryProvider()
    provider.configs.clear()
    paths.member_mail_dir("human:unregistered").mkdir(parents=True)
    resolver = RecipientResolver(provider, paths, lambda _: "gateway:github")
    expected = [{"id": "rika", "name": "rika", "address": "rika@tlon.internal"}]
    assert resolver.list_members() == expected
    assert RecipientResolver(provider, paths).list_members() == expected


def test_directory_query_does_not_initialize_database(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    resolver = RecipientResolver(DirectoryProvider(), paths)
    assert resolver.list_members() == [
        {"id": "alice", "name": "Alice", "address": "alice@tlon.internal"}
    ]
    assert not paths.mail_database_file().exists()
