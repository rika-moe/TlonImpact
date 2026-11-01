"""Acceptance and delivery recover independently without regenerating mail content."""

from pathlib import Path

import pytest

from tlon.gitea.webhook_store import MailPlan, WebhookStore, stable_mail_id


def plan(key: str = "event", member: str = "member") -> MailPlan:
    return MailPlan(
        member,
        stable_mail_id(key, member),
        "member@tlon",
        "2026-11-01T00:00:00Z",
        "subject",
        "body",
    )


def test_reopen_recovers_exact_plan_and_redelivery_does_not_replace_it(tmp_path: Path) -> None:
    database = tmp_path / "inbox.db"
    store = WebhookStore(database)
    store.initialize()
    expected = plan()
    assert store.accept("event", "delivery1", b"original", [expected])
    recovered = WebhookStore(database)
    recovered.initialize()
    assert recovered.pending()[0].plan == expected
    assert not recovered.accept("event", "delivery2", b"redelivery", [plan(member="other")])
    assert recovered.pending()[0].plan == expected
    recovered.complete("event", "member")
    assert recovered.pending() == []
    assert not recovered.accept("event", "delivery3", b"original", [expected])
    assert recovered.pending() == []
    assert recovered.status()["deliveries"] == 3


def test_delivery_id_collision_rejects_body_and_event_changes(tmp_path: Path) -> None:
    store = WebhookStore(tmp_path / "inbox.db")
    store.initialize()
    store.accept("event", "same", b"body", [plan()])
    assert not store.accept("event", "same", b"body", [plan()])
    for key, body in [("event", b"changed"), ("other", b"body")]:
        with pytest.raises(ValueError, match="reused"):
            store.accept(key, "same", body, [plan()])
    assert store.status()["accepted"] == 1


def test_failure_remains_pending_and_skipped_finishes(tmp_path: Path) -> None:
    store = WebhookStore(tmp_path / "inbox.db")
    store.initialize()
    store.accept("event", "one", b"{}", [plan()])
    store.fail("event", "member", "Mail delivery unavailable")
    assert store.status()["failed"] == 1
    assert store.pending()[0].plan == plan()
    store.complete("event", "member", outcome="skipped")
    assert store.status()["failed"] == 0
    assert store.pending() == []
    store.accept("unrouted", "two", b"{}", [])
    assert store.status()["unrouted"] == 1
    assert "payload" not in str(store.status())


def test_stable_mail_id_has_unambiguous_components() -> None:
    assert stable_mail_id("ab", "c") != stable_mail_id("a", "bc")
    assert stable_mail_id("event", "member") == stable_mail_id("event", "member")


def test_concurrent_redelivery_has_one_immutable_outgoing_plan(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    store = WebhookStore(tmp_path / "inbox.db")
    store.initialize()
    with ThreadPoolExecutor(max_workers=4) as executor:
        accepted = list(
            executor.map(
                lambda number: store.accept("event", f"delivery{number}", b"body", [plan()]),
                range(8),
            )
        )
    assert sum(accepted) == 1
    assert len(store.pending()) == 1
    assert store.status()["deliveries"] == 8


def test_plan_serialization_failure_rolls_back_event_and_delivery(tmp_path: Path) -> None:
    store = WebhookStore(tmp_path / "inbox.db")
    store.initialize()
    # A malformed caller plan must not acknowledge an event without its queue record.
    invalid = MailPlan("member", "id", "address", "time", "subject", object())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        store.accept("event", "delivery", b"body", [invalid])
    assert store.status()["accepted"] == 0
    assert store.status()["deliveries"] == 0
    assert store.accept("event", "delivery", b"body", [plan()])


def test_failed_recipient_does_not_starve_later_unattempted_mail(tmp_path: Path) -> None:
    store = WebhookStore(tmp_path / "inbox.db")
    store.initialize()
    store.accept("first", "one", b"one", [plan("first")])
    store.accept("second", "two", b"two", [plan("second")])
    store.fail("first", "member", "Temporarily unavailable")
    assert store.pending(limit=1)[0].event_key == "second"


def test_partial_unrouted_usernames_survive_restart_and_redelivery(tmp_path: Path) -> None:
    path = tmp_path / "inbox.db"
    store = WebhookStore(path)
    store.initialize()
    store.accept("event", "one", b"body", [plan()], unrouted=["unknown", "retired", "unknown"])
    reopened = WebhookStore(path)
    reopened.accept("event", "two", b"body", [], unrouted=["different"])
    status = reopened.status()
    assert status["unrouted"] == 0
    assert status["unrouted_users"] == 2
    assert status["recent_unrouted"] == [
        {"event_key": "event", "usernames": ["retired", "unknown"]}
    ]
    assert len(reopened.pending()) == 1
