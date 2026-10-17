"""Resolution uses member configuration and SQLite human registration."""

from pathlib import Path

from tlon.base.config import AgentConfig, AgentInfoProvider
from tlon.base.paths import TlonPaths
from tlon.organization.mail.resolver import RecipientResolver
from tlon.organization.mail.store import MailStore


class Provider(AgentInfoProvider):
    def get_config(self, agent_id: str) -> AgentConfig | None:
        return AgentConfig(id="alice", name="Alice") if agent_id == "alice" else None

    def get_all_ids(self) -> set[str]:
        return {"alice"}


def test_registered_humans_and_sleeping_agents_resolve(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    resolver = RecipientResolver(Provider(), paths)
    store = MailStore(paths.mail_database_file())
    store.register_member("human:bob")
    assert resolver.resolve_address("Alice") == "alice@tlon.internal"
    assert resolver.resolve_member("alice@tlon.internal") == "alice"
    assert resolver.resolve_address("human:bob") == "bob@tlon.internal"
    assert resolver.resolve_member("bob@tlon.internal") == "human:bob"
    assert resolver.resolve_address("ghost") is None


def test_legacy_directories_do_not_register_humans(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    paths.member_mail_dir("human:old").mkdir(parents=True)
    resolver = RecipientResolver(Provider(), paths)
    assert resolver.resolve_address("old") is None


def test_external_addresses_require_gateway_claim(tmp_path: Path) -> None:
    resolver = RecipientResolver(
        Provider(),
        TlonPaths(home=tmp_path),
        lambda address: "gateway:github" if address == "n@github.com" else None,
    )
    assert resolver.resolve_member("n@github.com") == "gateway:github"
    assert resolver.resolve_member("x@elsewhere.com") is None
