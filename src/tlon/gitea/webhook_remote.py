"""Reconcile instance callback hooks and read authoritative discussion participants."""

from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID

from tlon.gitea.control import StartupControl
from tlon.gitea.initialize import ORGANIZATION, request
from tlon.gitea.projects import ProjectManager
from tlon.gitea.storage import GiteaError
from tlon.gitea.webhook_events import WebhookEvent

HOOK_EVENTS = [
    "issues",
    "issue_assign",
    "issue_comment",
    "pull_request",
    "pull_request_assign",
    "pull_request_review_request",
    "pull_request_comment",
    "pull_request_review",
    "pull_request_sync",
]


class WebhookRepositoryError(GiteaError):
    """The callback names a repository outside current managed governance."""


class WebhookRemote:
    """Reconcile hooks without assuming the API can read or edit their secrets."""

    def __init__(self, url: str, password: str, control: StartupControl | None = None) -> None:
        self._url = url
        self._password = password
        self._control = control or StartupControl()

    def renew(self, control: StartupControl) -> None:
        self._control = control

    def _call(
        self, path: str, *, method: str = "GET", body: dict[str, object] | None = None
    ) -> tuple[int, Any]:
        self._control.check()
        try:
            result = request(self._url, path, self._password, method=method, body=body)
        except Exception:
            raise GiteaError("Gitea webhook request failed") from None
        self._control.check()
        return result

    def _list(self, path: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        page = 1
        while True:
            code, batch = self._call(f"{path}?limit=50&page={page}")
            if (
                code != 200
                or not isinstance(batch, list)
                or any(not isinstance(item, dict) for item in batch)
            ):
                raise GiteaError("Gitea webhook lookup failed")
            result.extend(batch)
            if len(batch) < 50:
                return result
            page += 1

    def _managed(self) -> dict[int, str]:
        projects = ProjectManager(self._url, self._password, control=self._control).list_projects()
        result: dict[int, str] = {}
        for project in projects:
            if project.get("managed") is not True:
                continue
            name = project["name"]
            code, repo = self._call(f"repos/{ORGANIZATION}/{quote(name, safe='')}")
            if (
                code != 200
                or not isinstance(repo, dict)
                or repo.get("private") is not True
                or type(repo.get("id")) is not int
            ):
                raise GiteaError("Gitea webhook repository is unavailable")
            result[repo["id"]] = name
        return result

    def validate(self, repository_id: int, name: str) -> None:
        managed_name = self._managed().get(repository_id)
        if managed_name is None or name != f"{ORGANIZATION}/{managed_name}":
            raise WebhookRepositoryError("Gitea webhook repository is not managed")

    def sync(self, callback_url: str, secret: str) -> dict[int, str]:
        """The instance UUID path owns a hook even when its host address changes."""
        callback = urlsplit(callback_url)
        try:
            prefix, instance_id = callback.path.rsplit("/", 1)
            UUID(instance_id)
        except ValueError:
            raise GiteaError("Invalid Gitea instance callback URL") from None
        if prefix != "/gitea" or callback.scheme not in {"http", "https"} or not callback.hostname:
            raise GiteaError("Invalid Gitea instance callback URL")
        managed = self._managed()
        for name in managed.values():
            path = f"repos/{ORGANIZATION}/{quote(name, safe='')}/hooks"
            matches = []
            for hook in self._list(path):
                config = hook.get("config")
                existing_url = config.get("url") if isinstance(config, dict) else None
                if not isinstance(existing_url, str):
                    continue
                try:
                    existing = urlsplit(existing_url)
                except ValueError:
                    continue
                if existing.path != callback.path:
                    continue
                if existing.scheme not in {"http", "https"} or hook.get("type") != "gitea":
                    raise GiteaError("Gitea instance callback hook has conflicting ownership")
                matches.append(hook)
            if len(matches) > 1:
                raise GiteaError("Gitea has duplicate instance callback hooks")
            options: dict[str, object] = {
                "type": "gitea",
                "active": True,
                "events": HOOK_EVENTS,
                "config": {"url": callback_url, "content_type": "json", "secret": secret},
            }
            if matches:
                hook = matches[0]
                hook_id = hook.get("id")
                if type(hook_id) is not int or hook.get("type") != "gitea":
                    raise GiteaError("Gitea instance callback hook has conflicting ownership")
                if (
                    hook.get("active") is True
                    and hook["config"].get("url") == callback_url
                    and set(HOOK_EVENTS).issubset(set(hook.get("events") or []))
                    and hook["config"].get("content_type") == "json"
                ):
                    continue
                # Gitea 28.2 ignores secret on PATCH. The secret is fixed at creation.
                options["config"] = {"url": callback_url, "content_type": "json"}
                code, saved = self._call(f"{path}/{hook_id}", method="PATCH", body=options)
            else:
                code, saved = self._call(path, method="POST", body=options)
            if (
                code not in {200, 201}
                or not isinstance(saved, dict)
                or type(saved.get("id")) is not int
            ):
                raise GiteaError("Gitea webhook configuration failed")
        return managed

    def history(self, event: WebhookEvent) -> list[str]:
        """Read every page; private forks need not be readable by individual reviewers."""
        path = f"repos/{ORGANIZATION}/{quote(event.repository.removeprefix(ORGANIZATION + '/'), safe='')}"
        index = event.object_index
        participants: set[str] = set()
        objects = self._list(f"{path}/issues/{index}/comments")
        if event.is_pull:
            objects.extend(self._list(f"{path}/pulls/{index}/reviews"))
        code, issue = self._call(f"{path}/issues/{index}")
        if code != 200 or not isinstance(issue, dict):
            raise GiteaError("Gitea discussion lookup failed")
        objects.append(issue)
        users = [obj.get("user") for obj in objects]
        users.extend(issue.get("assignees") or [])
        if event.is_pull:
            code, pull = self._call(f"{path}/pulls/{index}")
            if code != 200 or not isinstance(pull, dict):
                raise GiteaError("Gitea review lookup failed")
            users.extend(pull.get("requested_reviewers") or [])
        for user in users:
            if isinstance(user, dict):
                name = user.get("login") or user.get("username")
                if isinstance(name, str) and name:
                    participants.add(name)
        return sorted(participants)
