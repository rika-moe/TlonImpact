"""Member reconciliation preserves identity and fails closed on partial changes."""

import json
from pathlib import Path
from typing import Any

import pytest

from tlon.base.paths import TlonPaths
from tlon.gitea.members import TOKEN_NAME, MemberManager, member_username
from tlon.gitea.storage import GiteaError


class FakeAPI:
    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.tokens: dict[str, list[dict[str, Any]]] = {}
        self.members: set[str] = set()
        self.next_id = 1
        self.fail_delete = False
        self.lose_token_response = False

    def __call__(
        self,
        url: str,
        path: str,
        password: str,
        *,
        username: str = "tlon-service",
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        body = body or {}
        path = path.split("?")[0]
        bits = path.split("/")
        if path == "admin/users":
            name = body["username"]
            self.users[name] = dict(body, login=name, is_admin=False)
            self.tokens[name] = []
            return 201, self.users[name]
        if path == "user":
            user = self.users[username]
            if user.get("prohibit_login") or user["password"] != password:
                return 401, None
            return 200, user
        if bits[:2] == ["admin", "users"]:
            user = self.users.get(bits[2])
            if user is None:
                return 404, None
            user.update(body)
            return 200, user
        if path == "orgs/tlon/teams":
            return 200, [{"id": 4, "name": "members"}]
        if bits[0] == "teams":
            self.members.add(bits[-1])
            return 204, None
        if bits[:3] == ["orgs", "tlon", "members"]:
            self.members.discard(bits[-1])
            return 204, None
        if bits[0] == "users":
            name = bits[1]
            if len(bits) == 2:
                return (200, self.users[name]) if name in self.users else (404, None)
            tokens = self.tokens[name]
            if method == "GET":
                return 200, [dict(t, sha1="") for t in tokens]
            if method == "DELETE":
                if self.fail_delete:
                    raise RuntimeError("SECRET token response")
                tokens[:] = [t for t in tokens if str(t["id"]) != bits[-1]]
                return 204, None
            token = {
                "id": self.next_id,
                "name": body["name"],
                "scopes": body["scopes"],
                "sha1": f"secret-token-{self.next_id:032d}",
            }
            token["token_last_eight"] = token["sha1"][-8:]
            self.next_id += 1
            tokens.append(token)
            if self.lose_token_response:
                self.lose_token_response = False
                raise RuntimeError("SECRET lost response")
            return 201, token
        raise AssertionError(path)


@pytest.fixture
def manager(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[MemberManager, FakeAPI, TlonPaths]:
    api = FakeAPI()
    monkeypatch.setattr("tlon.gitea.members.request", api)
    paths = TlonPaths(tmp_path)
    return MemberManager(paths, "http://test/", "admin-secret"), api, paths


def test_reuse_rename_restart_and_distinct_successor(
    manager: tuple[MemberManager, FakeAPI, TlonPaths],
) -> None:
    m, api, paths = manager
    assert m.reconcile({"stable": "Alice", "other": "Alice"}) == {}
    original = m.credentials("stable")
    assert original.username != m.credentials("other").username
    assert m.reconcile({"stable": "Renamed", "other": "Alice"}) == {}
    assert m.credentials("stable") == original
    restarted = MemberManager(paths, "http://test/", "admin-secret")
    with pytest.raises(GiteaError):
        restarted.credentials("stable")
    assert restarted.reconcile({"stable": "Renamed", "other": "Alice"}) == {}
    assert restarted.credentials("stable") == original
    assert len(api.users) == 2
    assert api.users[original.username]["full_name"] == "Renamed"
    assert (
        paths.gitea_dir().joinpath("members", original.username + ".json").stat().st_mode & 0o777
        == 0o600
    )


def test_retirement_failure_closed_and_retry(
    manager: tuple[MemberManager, FakeAPI, TlonPaths],
) -> None:
    m, api, _ = manager
    assert m.reconcile({"id": "Alice"}) == {}
    user = m.credentials("id").username
    api.tokens[user].append(dict(api.tokens[user][0], id=999, name="other-token"))
    api.fail_delete = True
    errors = m.reconcile({})
    assert "id" in errors and "SECRET" not in str(errors)
    assert api.users[user]["prohibit_login"]
    with pytest.raises(GiteaError):
        m.credentials("id")
    api.fail_delete = False
    assert m.reconcile({}) == {}
    assert api.tokens[user] == [] and user not in api.members
    assert "id" in m.reconcile({"id": "Alice", "successor": "Alice"})
    assert m.credentials("successor").username != user
    assert api.users[user]["prohibit_login"]


def test_lost_token_response_recovers_without_duplicate(
    manager: tuple[MemberManager, FakeAPI, TlonPaths],
) -> None:
    m, api, _ = manager
    api.lose_token_response = True
    assert "id" in m.reconcile({"id": "Alice"})
    with pytest.raises(GiteaError):
        m.credentials("id")
    assert m.reconcile({"id": "Alice"}) == {}
    tokens = api.tokens[m.credentials("id").username]
    assert len(tokens) == 1 and tokens[0]["name"] == TOKEN_NAME


def test_token_revoked_externally_is_recreated(
    manager: tuple[MemberManager, FakeAPI, TlonPaths],
) -> None:
    m, api, _ = manager
    m.reconcile({"id": "Alice"})
    before = m.credentials("id")
    api.tokens[before.username].clear()
    assert m.reconcile({"id": "Alice"}) == {}
    assert m.credentials("id").token != before.token


def test_account_ownership_mismatch_does_not_reset_password(
    manager: tuple[MemberManager, FakeAPI, TlonPaths],
) -> None:
    m, api, _ = manager
    name = member_username("id")
    api.users[name] = {"login": name, "password": "foreign", "is_admin": False}
    assert "id" in m.reconcile({"id": "Alice"})
    assert api.users[name]["password"] == "foreign"
    assert name not in api.members


def test_corrupt_record_fails_closed(manager: tuple[MemberManager, FakeAPI, TlonPaths]) -> None:
    m, _, paths = manager
    m.reconcile({"id": "Alice"})
    record = paths.gitea_dir() / "members" / (member_username("id") + ".json")
    data = json.loads(record.read_text())
    data["member_id"] = "changed"
    record.write_text(json.dumps(data))
    with pytest.raises(GiteaError, match="storage"):
        m.reconcile({"id": "Alice"})
    with pytest.raises(GiteaError):
        m.credentials("id")


def test_steady_state_does_not_rewrite_secrets(
    manager: tuple[MemberManager, FakeAPI, TlonPaths], monkeypatch: pytest.MonkeyPatch
) -> None:
    m, _, _ = manager
    assert m.reconcile({"id": "Alice"}) == {}

    def unexpected_write(path: Path, value: dict[str, str | int]) -> None:
        pytest.fail("unchanged reconciliation rewrote credentials")

    monkeypatch.setattr("tlon.gitea.members.write_private", unexpected_write)
    assert m.reconcile({"id": "Alice"}) == {}


def test_retirement_runs_before_active_provisioning(
    manager: tuple[MemberManager, FakeAPI, TlonPaths], monkeypatch: pytest.MonkeyPatch
) -> None:
    m, api, _ = manager
    assert m.reconcile({"active": "A", "retiring": "B"}) == {}
    original = m._activate

    def assert_retired(path: Path, record: dict[str, str | int], display_name: str) -> None:
        assert api.users[member_username("retiring")]["prohibit_login"]
        original(path, record, display_name)

    monkeypatch.setattr(m, "_activate", assert_retired)
    assert m.reconcile({"active": "A"}) == {}


def test_reconcile_deadline_clears_cached_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tlon.gitea.control import StartupControl

    api = FakeAPI()
    monkeypatch.setattr("tlon.gitea.members.request", api)
    manager = MemberManager(TlonPaths(tmp_path), "http://localhost/", "host-secret")
    assert manager.reconcile({"member": "Name"}) == {}
    assert manager.credentials("member").token
    with pytest.raises(GiteaError, match="timed out"):
        manager.reconcile({"member": "Name"}, control=StartupControl(timeout=0))
    with pytest.raises(GiteaError):
        manager.credentials("member")
