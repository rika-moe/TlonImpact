"""The instance address survives browsers/restarts and cannot be silently replaced."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tlon_server.core.local_identity import LocalIdentity


@pytest.mark.parametrize(
    "username",
    [
        "",
        " alice",
        "alice ",
        "../alice",
        "a/b",
        "a\\b",
        "张三",
        "_alice",
        "a" * 65,
        "postmaster",
        "POSTMASTER",
        None,
        1,
    ],
)
def test_invalid_identity_does_not_create_file(tmp_path: Path, username: object) -> None:
    path = tmp_path / "identity.json"
    identity = LocalIdentity(path)
    with pytest.raises(ValueError):
        identity.initialize(username)  # type: ignore[arg-type]
    assert identity.username is None
    assert not path.exists()


def test_persistent_identity_and_single_initialization(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    identity = LocalIdentity(path)
    assert identity.username is None
    assert identity.address is None
    identity.initialize("xor.sum-1")
    reopened = LocalIdentity(path)
    assert reopened.username == "xor.sum-1"
    assert reopened.address == "xor.sum-1@tlon.internal"
    with pytest.raises(FileExistsError):
        reopened.initialize("someone-else")
    assert LocalIdentity(path).username == "xor.sum-1"


def test_concurrent_initialization_publishes_exactly_one_complete_identity(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    contenders = [LocalIdentity(path), LocalIdentity(path)]

    def initialize(index: int) -> bool:
        try:
            contenders[index].initialize(f"person-{index}")
        except FileExistsError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(initialize, [0, 1]))
    assert sum(results) == 1
    assert LocalIdentity(path).username == f"person-{results.index(True)}"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(
    "data", ["{", "null", "{}", '{"username":"../evil"}', '{"username":"alice","extra":1}']
)
def test_corrupt_identity_fails_explicitly(tmp_path: Path, data: str) -> None:
    path = tmp_path / "identity.json"
    path.write_text(data)
    with pytest.raises(RuntimeError, match="identity"):
        LocalIdentity(path)
    assert path.read_text() == data
