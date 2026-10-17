"""Mailbox pagination and explicit incoming-only read-state regressions."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.identity_helpers import make_identity
from tlon.api.mail import MailFacade
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.store import MailStore
from tlon_server.api.rest import RESTAPIHandler


class Request:
    def __init__(self, body: Any = None, query: dict[str, str] | None = None) -> None:
        self.body = body
        self.query = query or {}
        self.match_info: dict[str, str] = {}

    async def json(self) -> Any:
        return self.body


def setup(tmp_path: Path) -> tuple[MailStore, RESTAPIHandler]:
    paths = TlonPaths(home=tmp_path)
    return MailStore(paths.mail_database_file()), RESTAPIHandler(
        None,
        identity=make_identity(),
        mail=MailFacade(adapter_manager=MagicMock(), agent_registry=MagicMock(), tlon_paths=paths),
    )


def letter(number: int, thread_id: str | None = None) -> Mail:
    return Mail.create(
        from_address="agent@tlon.internal",
        to=["alice@tlon.internal"],
        content=str(number),
        thread_id=thread_id,
    )


async def test_same_timestamp_pagination_and_cursor_visibility(tmp_path: Path) -> None:
    store, handler = setup(tmp_path)
    mails = [letter(i) for i in range(7)]
    for mail in mails:
        mail.timestamp = mails[0].timestamp
        store.add_to_inbox(mail, "human:alice")
    seen: list[str] = []
    query = {"limit": "2"}
    while True:
        response = await handler.handle_inbox(Request(query=query))
        assert response.status == 200
        body = json.loads(response.text)
        seen = [m["id"] for m in body["mails"]] + seen
        if not body["has_more"]:
            assert body["next_before"] is None
            break
        query["before"] = body["next_before"]
    assert seen == [m.id for m in mails]
    foreign = letter(9)
    store.add_to_inbox(foreign, "human:bob")
    for cursor in (foreign.id, "missing", ""):
        response = await handler.handle_inbox(Request(query={"before": cursor}))
        assert response.status == 400
        assert json.loads(response.text)["reason"] == "invalid_cursor"
        request = Request(query={"before": cursor})
        request.match_info["member_id"] = "human:alice"
        observed = await handler.handle_member_mail(request)
        assert observed.status == 400
        assert json.loads(observed.text)["reason"] == "invalid_cursor"


async def test_thread_read_covers_unloaded_history_and_only_own_incoming(tmp_path: Path) -> None:
    store, handler = setup(tmp_path)
    root = letter(0)
    mails = [root, letter(1, root.thread_id), letter(2, root.thread_id)]
    for mail in mails:
        store.add_to_inbox(mail, "human:alice")
        store.add_to_inbox(mail, "agent")
    sent = letter(3, root.thread_id)
    store.append_sent(sent, "human:alice")
    response = await handler.handle_inbox(Request(query={"limit": "1"}))
    assert json.loads(response.text)["count"] == 1
    for read in (True, False):
        response = await handler.handle_inbox_read(
            Request({"read": read, "thread_id": root.thread_id})
        )
        assert response.status == 200
        assert json.loads(response.text) == {"mail_ids": [m.id for m in mails], "read": read}
        reopened = MailStore(store.database)
        assert [m.id for m in reopened.mails("human:alice", "unread")] == (
            [] if read else [m.id for m in mails]
        )
        assert len(reopened.mails("agent", "unread")) == 3
    response = await handler.handle_inbox_read(Request({"read": False, "mail_id": sent.id}))
    assert response.status == 404
    assert json.loads(response.text)["reason"] == "mail_not_found"
    response = await handler.handle_inbox_read(
        Request({"username": "bob", "read": True, "mail_id": root.id})
    )
    assert response.status == 400
    assert json.loads(response.text)["reason"] == "identity_managed"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (None, "invalid_body"),
        ([], "invalid_body"),
        ({}, "invalid_read"),
        ({"read": 1, "mail_id": "x"}, "invalid_read"),
        ({"username": "../alice", "read": True, "mail_id": "x"}, "identity_managed"),
        ({"read": False}, "invalid_selector"),
        ({"read": True, "mail_id": "x", "thread_id": "x"}, "invalid_selector"),
        ({"read": True, "mail_id": None}, "invalid_mail_id"),
        ({"read": True, "mail_id": " "}, "invalid_mail_id"),
        ({"read": True, "thread_id": " "}, "invalid_thread_id"),
    ],
)
async def test_read_validation(tmp_path: Path, body: Any, reason: str) -> None:
    _, handler = setup(tmp_path)
    response = await handler.handle_inbox_read(Request(body))
    assert response.status == 400
    assert json.loads(response.text)["reason"] == reason


async def test_read_invalid_json_has_machine_reason(tmp_path: Path) -> None:
    class InvalidJSON(Request):
        async def json(self) -> Any:
            raise json.JSONDecodeError("invalid", "{", 0)

    _, handler = setup(tmp_path)
    response = await handler.handle_inbox_read(InvalidJSON())
    assert response.status == 400
    assert json.loads(response.text)["reason"] == "invalid_json"


def test_missing_database_page_does_not_create_database(tmp_path: Path) -> None:
    store = MailStore(tmp_path / "mail.sqlite3")
    assert store.page("agent") == {"mails": [], "count": 0, "has_more": False, "next_before": None}
    assert not store.database.exists()


@pytest.mark.parametrize("limit", ["0", "-1"])
@pytest.mark.parametrize("observer", [False, True])
async def test_nonpositive_limit_is_not_a_cursor_error(
    tmp_path: Path, limit: str, observer: bool
) -> None:
    store, handler = setup(tmp_path)
    store.register_member("human:alice")
    request = Request(query={"limit": limit})
    request.match_info["member_id"] = "human:alice"
    response = await (
        handler.handle_member_mail(request) if observer else handler.handle_inbox(request)
    )
    assert response.status == 400
    assert json.loads(response.text) == {
        "error": "limit must be positive",
        "reason": "invalid_limit",
    }


async def test_cursor_ignores_newer_insert_between_pages(tmp_path: Path) -> None:
    store, handler = setup(tmp_path)
    mails = [letter(i) for i in range(5)]
    for mail in mails:
        store.add_to_inbox(mail, "human:alice")
    first = json.loads((await handler.handle_inbox(Request(query={"limit": "2"}))).text)
    newer = letter(9)
    store.add_to_inbox(newer, "human:alice")
    second = json.loads(
        (
            await handler.handle_inbox(
                Request(query={"limit": "10", "before": first["next_before"]})
            )
        ).text
    )
    assert [m["id"] for m in second["mails"] + first["mails"]] == [m.id for m in mails]
    assert newer.id not in [m["id"] for m in second["mails"]]


async def test_single_mail_read_and_missing_target_preserve_other_state(tmp_path: Path) -> None:
    store, handler = setup(tmp_path)
    a, b = letter(1), letter(2)
    for mail in (a, b):
        store.add_to_inbox(mail, "human:alice")
    response = await handler.handle_inbox_read(Request({"read": True, "mail_id": a.id}))
    assert response.status == 200
    assert json.loads(response.text)["mail_ids"] == [a.id]
    response = await handler.handle_inbox_read(Request({"read": False, "thread_id": "missing"}))
    assert response.status == 404
    reopened = MailStore(store.database)
    assert [m.id for m in reopened.mails("human:alice", "unread")] == [b.id]


async def test_read_username_query_cannot_override_identity(tmp_path: Path) -> None:
    _, handler = setup(tmp_path)
    request = Request({"read": True, "mail_id": "some-mail"}, query={"username": "bob"})
    response = await handler.handle_inbox_read(request)
    assert response.status == 400
    assert json.loads(response.text)["reason"] == "identity_managed"
