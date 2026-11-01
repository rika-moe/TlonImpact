"""Interpret Gitea discussion events without choosing Tlon identities or sending mail."""

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

EVENT_TYPES = frozenset(
    {
        "issues",
        "issue_assign",
        "pull_request_assign",
        "pull_request_review_request",
        "pull_request_sync",
        "issue_comment",
        "pull_request",
        "pull_request_comment",
        "pull_request_review",
        "pull_request_review_approved",
        "pull_request_review_rejected",
        "pull_request_review_comment",
    }
)
ACTIONS = frozenset(
    {
        "opened",
        "edited",
        "assigned",
        "unassigned",
        "review_requested",
        "review_request_removed",
        "reviewed",
        "closed",
        "reopened",
        "created",
        "submitted",
        "synchronized",
    }
)


def _user(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    result = value.get("login") or value.get("username")
    return result if isinstance(result, str) else ""


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class WebhookEvent:
    event_key: str
    event_type: str
    action: str
    repository: str
    repository_id: int
    object_index: int
    is_pull: bool
    actor: str
    title: str
    url: str
    text: str
    commit: str
    participants: tuple[str, ...]

    def recipients(self, history_usernames: Iterable[str] = ()) -> tuple[str, ...]:
        names = {name.casefold() for name in (*self.participants, *history_usernames)}
        names.discard("")
        return tuple(sorted(name for name in names if name.casefold() != self.actor.casefold()))


def parse_event(event_type: str, payload: dict[str, Any]) -> WebhookEvent | None:
    """Ignore irrelevant events; reject incomplete actionable discussion payloads."""
    if event_type not in EVENT_TYPES:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("action"), str):
        raise ValueError("Webhook action is required")
    action = payload["action"]
    if action not in ACTIONS:
        return None
    repo = payload.get("repository")
    obj = payload.get("pull_request") or payload.get("issue")
    if not isinstance(repo, dict) or not isinstance(obj, dict):
        raise ValueError("Webhook repository and discussion are required")
    for container in (payload, obj):
        for key in (
            "user",
            "sender",
            "requested_reviewer",
            "assignee",
            "head",
            "comment",
            "review",
            "changes",
        ):
            if container.get(key) is not None and not isinstance(container[key], dict):
                raise ValueError("Webhook object field is invalid")
    for key in ("assignees", "requested_reviewers"):
        values = obj.get(key)
        if values is not None and (
            not isinstance(values, list) or any(not isinstance(value, dict) for value in values)
        ):
            raise ValueError("Webhook participant list is invalid")
    if not isinstance(repo.get("full_name"), str) or not repo["full_name"]:
        raise ValueError("Webhook repository name is required")
    repository_id = repo.get("id")
    index = obj.get("number") or payload.get("number")
    object_id = obj.get("id")
    actor = _user(payload.get("sender"))
    if (
        any(type(value) is not int or value <= 0 for value in (repository_id, index, object_id))
        or not actor
    ):
        raise ValueError("Webhook repository, discussion and actor IDs are invalid")
    comment = payload.get("comment")
    review = payload.get("review")
    if event_type in {"issue_comment", "pull_request_comment"} and (
        not isinstance(comment, dict) or type(comment.get("id")) is not int or comment["id"] <= 0
    ):
        raise ValueError("Webhook comment identity is required")
    if event_type in {
        "pull_request_review",
        "pull_request_review_approved",
        "pull_request_review_rejected",
        "pull_request_review_comment",
    } and (not isinstance(review, dict) or not isinstance(review.get("type"), str)):
        raise ValueError("Webhook review is required")
    content = comment if isinstance(comment, dict) else review if isinstance(review, dict) else obj
    for item in (obj, content):
        for key in (
            "body",
            "content",
            "title",
            "html_url",
            "updated_at",
            "submitted_at",
            "created_at",
        ):
            if item.get(key) is not None and not isinstance(item[key], str):
                raise ValueError("Webhook text field is invalid")
    text = _text(content.get("body")) or _text(content.get("content"))
    commit = _text(payload.get("commit_id")) or _text(obj.get("merge_commit_sha"))
    if not commit and isinstance(obj.get("head"), dict):
        commit = _text(obj["head"].get("sha"))
    is_pull = bool(payload.get("pull_request") or obj.get("pull_request") or payload.get("is_pull"))
    if action == "closed" and obj.get("merged") is True:
        action = "merged"
    participants = {
        _user(obj.get("user")),
        _user(payload.get("requested_reviewer")),
        _user(payload.get("assignee")),
    }
    for key in ("assignees", "requested_reviewers"):
        values = obj.get(key)
        if isinstance(values, list):
            participants.update(_user(value) for value in values)
    participants.add(_user(obj.get("assignee")))
    participants.add(_user(content.get("user")))
    # Only the current contribution carries mentions, not the parent issue description.
    if content is not obj or action in {"opened", "edited"}:
        participants.update(re.findall(r"(?<![\w@])@([A-Za-z0-9][A-Za-z0-9_.-]*)(?![\w./-])", text))
    version = (
        content.get("updated_at")
        or content.get("submitted_at")
        or content.get("created_at")
        or obj.get("updated_at")
    )
    semantic = [
        repository_id,
        object_id,
        event_type,
        action,
        content.get("id"),
        version,
        hashlib.sha256(text.encode()).hexdigest(),
        actor,
        commit,
        payload.get("changes"),
        content.get("type"),
        _user(payload.get("requested_reviewer")),
        sorted(_user(value) for value in obj.get("assignees") or [])
        if action in {"assigned", "unassigned"}
        else None,
    ]
    key = hashlib.sha256(
        json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return WebhookEvent(
        key,
        event_type,
        action,
        _text(repo.get("full_name")),
        cast(int, repository_id),
        cast(int, index),
        is_pull,
        actor,
        _text(obj.get("title")),
        _text(content.get("html_url")) or _text(obj.get("html_url")),
        text,
        commit,
        tuple(sorted(participants - {""})),
    )
