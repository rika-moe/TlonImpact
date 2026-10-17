"""Unit tests for the ``tlon dream`` loopback startup boundary."""

from unittest.mock import MagicMock

import pytest

from tlon_server.serverapp import _LOOPBACK_HOSTS, ServerApp, _startup_banner


@pytest.mark.parametrize("host", sorted(_LOOPBACK_HOSTS))
def test_loopback_bind_has_no_security_warning(host: str) -> None:
    """Loopback binds (127.0.0.1/localhost/::1) stay quiet."""
    assert "Security" not in _startup_banner(host, 8080)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "example.com"])
def test_non_loopback_bind_is_rejected(host: str) -> None:
    """Never expose the unauthenticated instance on wildcard/LAN addresses."""
    with pytest.raises(ValueError, match="loopback"):
        ServerApp(host=host, tlon_paths=MagicMock(), settings=MagicMock())
