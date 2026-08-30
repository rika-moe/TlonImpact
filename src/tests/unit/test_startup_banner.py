"""Unit tests for the ``tlon dream`` startup banner's security warning."""

import pytest

from tlon.app.server.serverapp import _LOOPBACK_HOSTS, _startup_banner


@pytest.mark.parametrize("host", sorted(_LOOPBACK_HOSTS))
def test_loopback_bind_has_no_security_warning(host: str) -> None:
    """Loopback binds (127.0.0.1/localhost/::1) stay quiet."""
    assert "Security" not in _startup_banner(host, 8080)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "example.com"])
def test_non_loopback_bind_warns(host: str) -> None:
    """Wildcard or LAN/public binds get a visible no-auth warning."""
    banner = _startup_banner(host, 8080)
    assert "Security" in banner
    assert "NO authentication" in banner
