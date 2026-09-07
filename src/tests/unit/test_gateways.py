"""Tests for the external gateway registry (organization/mail/gateways.py).

Issue  egress: a gateway claims an external address, after which the
shared resolver routes mail to the gateway's member id. Pins claim /
owner_of / re-claim-overwrites semantics.
"""

from tlon.organization.mail.gateways import ExternalGatewayRegistry

ADDRESS = "notifications@github.com"
GATEWAY = "gateway:github"


class TestExternalGatewayRegistry:
    def test_claim_then_owner_of(self):
        registry = ExternalGatewayRegistry()
        registry.claim(ADDRESS, GATEWAY)
        assert registry.owner_of(ADDRESS) == GATEWAY

    def test_unclaimed_address_has_no_owner(self):
        registry = ExternalGatewayRegistry()
        assert registry.owner_of(ADDRESS) is None
        assert registry.owner_of("nobody@example.com") is None

    def test_reclaim_overwrites(self):
        """Re-claiming an address overwrites the owner (logged warning)."""
        registry = ExternalGatewayRegistry()
        registry.claim(ADDRESS, "gateway:old")
        registry.claim(ADDRESS, GATEWAY)
        assert registry.owner_of(ADDRESS) == GATEWAY

    def test_multiple_gateways_coexist(self):
        registry = ExternalGatewayRegistry()
        registry.claim(ADDRESS, GATEWAY)
        registry.claim("alerts@example.com", "gateway:alerts")
        assert registry.owner_of(ADDRESS) == GATEWAY
        assert registry.owner_of("alerts@example.com") == "gateway:alerts"
