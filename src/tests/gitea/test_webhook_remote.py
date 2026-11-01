"""Remote reconciliation changes only this instance callback and reads all participants."""

from copy import deepcopy
from typing import Any

import pytest

from tlon.gitea import webhook_remote
from tlon.gitea.storage import GiteaError
from tlon.gitea.webhook_events import parse_event
from tlon.gitea.webhook_remote import WebhookRemote


class FakeRemote:
    def __init__(self) -> None:
        self.hooks: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []
        self.comment_pages: dict[int, list[dict[str, Any]]] = {1: []}
        self.repo = {"id": 12, "private": True}
        self.secrets: dict[int, str] = {}
        self.history: dict[int, list[str]] = {}

    def request(
        self,
        url: str,
        path: str,
        password: str,
        *,
        method: str = "GET",
        body: dict[str, object] | None = None,
    ) -> tuple[int, Any]:
        self.calls.append((method, path))
        if path == "repos/tlon/project":
            return 200, self.repo
        if path.startswith("repos/tlon/project/hooks"):
            if method == "GET":
                return 200, deepcopy(self.hooks)
            hook_id = int(path.rsplit("/", 1)[1]) if method == "PATCH" else 3
            if method == "POST":
                assert body is not None and isinstance(body["config"], dict)
                self.secrets[hook_id] = body["config"]["secret"]
                self.history[hook_id] = []
            saved = {"id": hook_id, **(body or {})}
            self.hooks = [hook for hook in self.hooks if hook["id"] != hook_id] + [saved]
            return (201 if method == "POST" else 200), deepcopy(saved)
        if "/comments?" in path:
            page = int(path.split("page=")[1])
            return 200, self.comment_pages.get(page, [])
        if "/reviews?" in path:
            return 200, [{"user": {"login": "reviewer"}}]
        if "/pulls/" in path:
            return 200, {"requested_reviewers": [{"login": "requested"}]}
        if "/issues/" in path:
            return 200, {"user": {"login": "author"}, "assignees": [{"login": "assigned"}]}
        raise AssertionError(path)


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> tuple[WebhookRemote, FakeRemote]:
    fake = FakeRemote()
    monkeypatch.setattr(webhook_remote, "request", fake.request)
    monkeypatch.setattr(
        webhook_remote.ProjectManager,
        "list_projects",
        lambda self: [{"name": "project", "managed": True}],
    )
    return WebhookRemote("http://gitea/", "password"), fake


def test_create_and_repeated_sync_does_not_rewrite_secret(
    remote: tuple[WebhookRemote, FakeRemote],
) -> None:
    manager, fake = remote
    fake.hooks = [{"id": 9, "type": "gitea", "config": {"url": "http://foreign/"}}]
    assert manager.sync("http://instance/gitea/12345678-1234-1234-1234-123456789abc", "secret") == {
        12: "project"
    }
    manager.sync("http://instance/gitea/12345678-1234-1234-1234-123456789abc", "secret")
    assert [method for method, _ in fake.calls if method != "GET"] == ["POST"]
    assert fake.hooks[0]["id"] == 9
    fake.hooks[1]["active"] = False
    manager.sync("http://instance/gitea/12345678-1234-1234-1234-123456789abc", "secret")
    assert [method for method, _ in fake.calls if method != "GET"] == ["POST", "PATCH"]
    assert "secret" not in fake.hooks[1]["config"]


def test_duplicate_exact_callback_is_conflict(remote: tuple[WebhookRemote, FakeRemote]) -> None:
    manager, fake = remote
    fake.hooks = [
        {
            "id": i,
            "type": "gitea",
            "config": {"url": "http://instance/gitea/12345678-1234-1234-1234-123456789abc"},
        }
        for i in (1, 2)
    ]
    with pytest.raises(GiteaError, match="duplicate"):
        manager.sync("http://instance/gitea/12345678-1234-1234-1234-123456789abc", "secret")
    assert all(method == "GET" for method, _ in fake.calls)


def test_validate_actual_private_and_id(remote: tuple[WebhookRemote, FakeRemote]) -> None:
    manager, fake = remote
    manager.validate(12, "tlon/project")
    with pytest.raises(webhook_remote.WebhookRepositoryError):
        manager.validate(12, "project")
    with pytest.raises(webhook_remote.WebhookRepositoryError):
        manager.validate(12, "other/project")
    with pytest.raises(GiteaError):
        manager.validate(13, "tlon/project")
    fake.repo["private"] = False
    with pytest.raises(GiteaError):
        manager.validate(12, "tlon/project")


def test_history_reads_comment_pages_and_pr_reviewers(
    remote: tuple[WebhookRemote, FakeRemote],
) -> None:
    manager, fake = remote
    fake.comment_pages = {
        1: [{"user": {"login": f"user{i}"}} for i in range(50)],
        2: [{"user": {"login": "last"}}],
    }
    event = parse_event(
        "pull_request",
        {
            "action": "opened",
            "repository": {"id": 12, "full_name": "tlon/project"},
            "pull_request": {"id": 9, "number": 2},
            "sender": {"login": "author"},
        },
    )
    assert event is not None
    participants = manager.history(event)
    assert {"last", "author", "assigned", "reviewer", "requested"}.issubset(participants)
    assert len(participants) == 55


def test_callback_address_change_updates_owned_hook_without_losing_secret_or_history(
    remote: tuple[WebhookRemote, FakeRemote],
) -> None:
    manager, fake = remote
    old_url = "http://192.168.5.2:3001/gitea/12345678-1234-1234-1234-123456789abc"
    new_url = "http://192.168.5.3:3011/gitea/12345678-1234-1234-1234-123456789abc"
    manager.sync(old_url, "original-secret")
    fake.history[3].append("failed-delivery")
    manager.sync(new_url, "original-secret")
    assert len(fake.hooks) == 1
    assert fake.hooks[0]["id"] == 3
    assert fake.hooks[0]["config"]["url"] == new_url
    assert fake.secrets[3] == "original-secret"
    assert fake.history[3] == ["failed-delivery"]
    assert [method for method, _ in fake.calls if method != "GET"] == ["POST", "PATCH"]


def test_two_addresses_of_same_instance_are_conflict(
    remote: tuple[WebhookRemote, FakeRemote],
) -> None:
    manager, fake = remote
    path = "/gitea/12345678-1234-1234-1234-123456789abc"
    fake.hooks = [
        {"id": i, "type": "gitea", "config": {"url": f"http://host{i}{path}"}} for i in (1, 2)
    ]
    with pytest.raises(GiteaError, match="duplicate"):
        manager.sync("http://newhost" + path, "secret")
    assert all(method == "GET" for method, _ in fake.calls)


@pytest.mark.parametrize(
    "url,hook_type",
    [
        ("ftp://host/gitea/12345678-1234-1234-1234-123456789abc", "gitea"),
        ("http://host/gitea/12345678-1234-1234-1234-123456789abc", "slack"),
    ],
)
def test_same_instance_with_conflicting_scheme_or_type_is_not_adopted(
    remote: tuple[WebhookRemote, FakeRemote],
    url: str,
    hook_type: str,
) -> None:
    manager, fake = remote
    fake.hooks = [{"id": 3, "type": hook_type, "config": {"url": url}}]
    with pytest.raises(GiteaError, match="ownership"):
        manager.sync("http://newhost/gitea/12345678-1234-1234-1234-123456789abc", "secret")
    assert all(method == "GET" for method, _ in fake.calls)
