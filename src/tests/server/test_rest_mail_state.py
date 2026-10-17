"""Mailbox pagination and explicit incoming-only read-state regressions."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tlon.api.mail import MailFacade
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.store import MailStore
from tlon_server.api.rest import RESTAPIHandler


class Request:
    def __init__(self, body: Any = None, query: dict[str, str] | None = None) -> None:
        self.body = body
        self.query = query or {}

    async def json(self) -> Any:
        return self.body


def setup(tmp_path: Path) -> tuple[MailStore, RESTAPIHandler]:
    paths = TlonPaths(home=tmp_path)
    return MailStore(paths.mail_database_file()), RESTAPIHandler(
        None,
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
    query = {"username": "alice", "limit": "2"}
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
        response = await handler.handle_inbox(
            Request(query={"username": "alice", "before": cursor})
        )
        assert response.status == 400


async def test_thread_read_covers_unloaded_history_and_only_own_incoming(tmp_path: Path) -> None:
    store, handler = setup(tmp_path)
    root = letter(0)
    mails = [root, letter(1, root.thread_id), letter(2, root.thread_id)]
    for mail in mails:
        store.add_to_inbox(mail, "human:alice")
        store.add_to_inbox(mail, "agent")
    sent = letter(3, root.thread_id)
    store.append_sent(sent, "human:alice")
    response = await handler.handle_inbox(Request(query={"username": "alice", "limit": "1"}))
    assert json.loads(response.text)["count"] == 1
    for read in (True, False):
        response = await handler.handle_inbox_read(
            Request({"username": "alice", "read": read, "thread_id": root.thread_id})
        )
        assert response.status == 200
        assert json.loads(response.text) == {"mail_ids": [m.id for m in mails], "read": read}
        reopened = MailStore(store.database)
        assert [m.id for m in reopened.mails("human:alice", "unread")] == (
            [] if read else [m.id for m in mails]
        )
        assert len(reopened.mails("agent", "unread")) == 3
    response = await handler.handle_inbox_read(
        Request({"username": "alice", "read": False, "mail_id": sent.id})
    )
    assert response.status == 404
    response = await handler.handle_inbox_read(
        Request({"username": "bob", "read": True, "mail_id": root.id})
    )
    assert response.status == 404


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"username": "alice", "read": 1, "mail_id": "x"},
        {"username": "../alice", "read": True, "mail_id": "x"},
        {"username": "alice", "read": False},
        {"username": "alice", "read": True, "mail_id": "x", "thread_id": "x"},
        {"username": "alice", "read": True, "mail_id": None},
        {"username": "alice", "read": True, "mail_id": " "},
    ],
)
async def test_read_validation(tmp_path: Path, body: Any) -> None:
    _, handler = setup(tmp_path)
    response = await handler.handle_inbox_read(Request(body))
    assert response.status == 400


def test_missing_database_page_does_not_create_database(tmp_path: Path) -> None:
    store = MailStore(tmp_path / "mail.sqlite3")
    assert store.page("agent") == {"mails": [], "count": 0, "has_more": False, "next_before": None}
    assert not store.database.exists()


async def test_cursor_ignores_newer_insert_between_pages(tmp_path: Path) -> None:
    store, handler = setup(tmp_path)
    mails = [letter(i) for i in range(5)]
    for mail in mails:
        store.add_to_inbox(mail, "human:alice")
    first = json.loads(
        (await handler.handle_inbox(Request(query={"username": "alice", "limit": "2"}))).text
    )
    newer = letter(9)
    store.add_to_inbox(newer, "human:alice")
    second = json.loads(
        (
            await handler.handle_inbox(
                Request(query={"username": "alice", "limit": "10", "before": first["next_before"]})
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
    response = await handler.handle_inbox_read(
        Request({"username": "alice", "read": True, "mail_id": a.id})
    )
    assert response.status == 200
    assert json.loads(response.text)["mail_ids"] == [a.id]
    response = await handler.handle_inbox_read(
        Request({"username": "alice", "read": False, "thread_id": "missing"})
    )
    assert response.status == 404
    reopened = MailStore(store.database)
    assert [m.id for m in reopened.mails("human:alice", "unread")] == [b.id]
