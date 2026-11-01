"""Gitea event identity excludes transport metadata and preserves contribution versions."""

from copy import deepcopy
from typing import Any

import pytest

from tlon.gitea.webhook_events import parse_event


def payload() -> dict[str, Any]:
    return {
        "action": "created",
        "repository": {"id": 1, "full_name": "tlon/project"},
        "sender": {"login": "writer"},
        "issue": {
            "id": 7,
            "number": 2,
            "title": "Issue",
            "user": {"login": "author"},
            "body": "old @unrelated",
            "html_url": "http://gitea/tlon/project/issues/2",
            "assignees": [{"login": "assigned"}],
        },
        "comment": {
            "id": 20,
            "body": "Hello @mentioned and @writer",
            "user": {"login": "writer"},
            "updated_at": "2026-11-01T00:00:00Z",
        },
    }


def test_route_current_comment_and_history_without_actor_or_parent_mentions() -> None:
    event = parse_event("issue_comment", payload())
    assert event is not None
    assert event.recipients(["participant", "WRITER"]) == (
        "assigned",
        "author",
        "mentioned",
        "participant",
    )
    assert event.repository_id == 1
    assert event.object_index == 2
    assert not event.is_pull


def test_comment_identity_and_edits_are_distinct_transport_fields_are_not() -> None:
    original = payload()
    event = parse_event("issue_comment", original)
    assert event is not None
    transported = deepcopy(original)
    transported["repository"]["updated_at"] = "later"
    transported["sender"]["avatar_url"] = "changed"
    assert parse_event("issue_comment", transported) == event
    for field, value in [("id", 21), ("body", "edited"), ("updated_at", "later")]:
        changed = deepcopy(original)
        changed["comment"][field] = value
        edited = parse_event("issue_comment", changed)
        assert edited is not None and edited.event_key != event.event_key


def test_review_request_and_merge() -> None:
    data = payload()
    del data["comment"]
    data["pull_request"] = data.pop("issue")
    data["requested_reviewer"] = {"login": "reviewer"}
    data["action"] = "review_requested"
    event = parse_event("pull_request", data)
    assert event is not None and event.is_pull
    assert "reviewer" in event.recipients()
    data["action"] = "closed"
    data["pull_request"]["merged"] = True
    merged = parse_event("pull_request", data)
    assert merged is not None and merged.action == "merged"


def test_ignore_push_and_reject_malformed_actionable_events() -> None:
    assert parse_event("push", {}) is None
    with pytest.raises(ValueError):
        parse_event("issues", {"action": "assigned"})


def test_native_review_content_mentions_and_review_type_identity() -> None:
    data = payload()
    del data["comment"]
    data["pull_request"] = data.pop("issue")
    data["pull_request"]["updated_at"] = "2026-11-01T00:00:00Z"
    data["review"] = {"type": "approved", "content": "Thanks @reader"}
    data["action"] = "reviewed"
    event = parse_event("pull_request_review_approved", data)
    assert event is not None and event.text == "Thanks @reader"
    assert "reader" in event.recipients()
    changed = deepcopy(data)
    changed["pull_request"]["updated_at"] = "2026-11-01T00:00:01Z"
    newer = parse_event("pull_request_review_approved", changed)
    assert newer is not None and newer.event_key != event.event_key


def test_concrete_assign_and_sync_do_not_remention_description() -> None:
    data = payload()
    del data["comment"]
    data["action"] = "assigned"
    event = parse_event("issue_assign", data)
    assert event is not None and event.recipients() == ("assigned", "author")
    data["pull_request"] = data.pop("issue")
    data["action"] = "synchronized"
    sync = parse_event("pull_request_sync", data)
    assert sync is not None and sync.is_pull
    assert "unrelated" not in sync.recipients()


@pytest.mark.parametrize(
    "field,value",
    [("assignees", {}), ("requested_reviewers", "user"), ("assignees", [7]), ("body", {})],
)
def test_malformed_fields_are_client_errors(field: str, value: Any) -> None:
    data = payload()
    data["issue"][field] = value
    with pytest.raises(ValueError):
        parse_event("issue_comment", data)


def test_mentions_and_history_are_casefolded_and_deduplicated() -> None:
    data = payload()
    data["comment"]["body"] = "@MENTIONED @mentioned @WRITER"
    event = parse_event("issue_comment", data)
    assert event is not None
    assert event.recipients(["MENTIONED", "AUTHOR", "WRITER"]) == (
        "assigned",
        "author",
        "mentioned",
    )


@pytest.mark.parametrize("event_type", ["issue_comment", "pull_request_review_approved"])
def test_contribution_payload_is_required(event_type: str) -> None:
    data = payload()
    del data["comment"]
    with pytest.raises(ValueError):
        parse_event(event_type, data)
