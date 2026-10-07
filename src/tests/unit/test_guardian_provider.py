"""Tests for the guardian-aware provider (organization/mail/guardian_provider.py).

Issue : the wrapper makes the built-in guardian resolvable —
``get_config("adam")`` answers a synthesized config — while
``get_all_ids`` delegates unchanged, so Adam stays out of the broadcast
expansion, the roster and every enumeration-driven scan. Resolution goes
through the shared RecipientResolver on both directions (speak-side
``resolve_address`` and delivery-side ``resolve_member``).
"""

import pytest

from tlon.base.config import AgentConfig
from tlon.base.paths import TlonPaths
from tlon.organization.mail.guardian_provider import GuardianAgentInfoProvider
from tlon.organization.mail.resolver import RecipientResolver


class MockAgentInfoProvider:
    """Mock AgentInfoProvider for testing."""

    def __init__(self, configs: dict[str, AgentConfig]):
        self._configs = configs

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)

    def get_all_ids(self) -> set[str]:
        return set(self._configs.keys())


@pytest.fixture
def delegate() -> MockAgentInfoProvider:
    return MockAgentInfoProvider({"a-001": AgentConfig(id="a-001", name="甲")})


@pytest.fixture
def provider(delegate: MockAgentInfoProvider) -> GuardianAgentInfoProvider:
    return GuardianAgentInfoProvider(delegate)


@pytest.fixture
def resolver(provider: GuardianAgentInfoProvider, tmp_path) -> RecipientResolver:
    return RecipientResolver(
        agent_info_provider=provider,
        is_human_online=lambda _username: False,
        tlon_paths=TlonPaths(home=tmp_path),
    )


class TestGuardianResolution:
    def test_adam_resolves_to_a_synthesized_config(self, provider: GuardianAgentInfoProvider):
        config = provider.get_config("adam")
        assert config is not None
        assert config.id == "adam"
        assert config.name == "Adam"

    def test_other_ids_delegate(
        self, provider: GuardianAgentInfoProvider, delegate: MockAgentInfoProvider
    ):
        assert provider.get_config("a-001") is delegate.get_config("a-001")
        assert provider.get_config("ghost") is None

    def test_get_all_ids_excludes_adam(self, provider: GuardianAgentInfoProvider):
        assert provider.get_all_ids() == {"a-001"}

    def test_delivery_side_resolves_adam(self, resolver: RecipientResolver):
        assert resolver.resolve_member("adam@tlon.internal") == "adam"

    def test_speak_side_resolves_adam_by_id(self, resolver: RecipientResolver):
        assert resolver.resolve_address("adam") == "adam@tlon.internal"
        assert resolver.resolve_address("adam@tlon.internal") == "adam@tlon.internal"

    def test_display_name_adam_not_supported(self, resolver: RecipientResolver):
        """Addressing by the display name "Adam" is unsupported — name
        lookup enumerates get_all_ids, which deliberately excludes adam."""
        assert resolver.resolve_address("Adam") is None


class TestGuardianWriteCapabilities:
    async def test_reload_adam_is_a_noop(self, provider: GuardianAgentInfoProvider):
        assert await provider.reload_agent("adam") is False

    def test_delete_adam_is_refused(self, provider: GuardianAgentInfoProvider):
        with pytest.raises(NotImplementedError):
            provider.delete_agent("adam")
