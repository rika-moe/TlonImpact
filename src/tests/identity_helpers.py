"""Isolated, initialized instance identities for direct REST handler tests."""

from pathlib import Path
from tempfile import TemporaryDirectory

from tlon_server.core.local_identity import LocalIdentity


def make_identity(username: str = "alice") -> LocalIdentity:
    """Build a loaded identity without retaining test files after construction."""
    with TemporaryDirectory(prefix="tlon-test-identity-") as directory:
        identity = LocalIdentity(Path(directory) / "identity.json")
        identity.initialize(username)
        return identity
