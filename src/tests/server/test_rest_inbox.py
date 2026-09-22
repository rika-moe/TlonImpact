"""Unit tests for the REST /inbox endpoint (mail model, R2).

No HTTP server is started: handle_inbox is called directly with a stub
request, against a MailFacade rooted at a tmp dir.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tlon.api.mail import MailFacade
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon_server.api.rest import RESTAPIHandler


class StubRequest:
    """Minimal stand-in for aiohttp.web.Request (query access only)."""

    def __init__(self, query: dict[str, str] | None = None):
        self.query = query or {}


@pytest.fixture
def paths(tmp_path: Path) -> TlonPaths:
    return TlonPaths(home=tmp_path)


@pytest.fixture
def handler(paths: TlonPaths) -> RESTAPIHandler:
    return RESTAPIHandler(
        None, mail=MailFacade(adapter_manager=MagicMock(), tlon_paths=paths)
    )


async def _seed_mailbox(paths: TlonPaths, username: str, count: int = 3) -> list[Mail]:
    """Write a few mails into the member's inbox and return them."""
    mailbox = Mailbox(paths.member_mail_dir(f"human:{username}"))
    mails = [
        Mail.create(
            from_address="gu-yanxi@tlon.internal",
            to=[f"{username}@tlon.internal"],
            content=f"letter {i}",
        )
        for i in range(count)
    ]
    for mail in mails:
        await mailbox.append_inbox(mail)
    return mails


class TestHandleInbox:
    """Tests for GET /inbox."""

    async def test_returns_member_mail(self, paths: TlonPaths, handler: RESTAPIHandler):
        seeded = await _seed_mailbox(paths, "alice")

        response = await handler.handle_inbox(StubRequest({"username": "alice"}))

        assert response.status == 200
        body = json.loads(response.text)
        assert body["count"] == len(seeded)
        assert [m["id"] for m in body["mails"]] == [m.id for m in seeded]

    async def test_defaults_to_guest(self, paths: TlonPaths, handler: RESTAPIHandler):
        await _seed_mailbox(paths, "guest")

        response = await handler.handle_inbox(StubRequest())

        assert response.status == 200
        body = json.loads(response.text)
        assert body["count"] == 3

    async def test_empty_inbox(self, handler: RESTAPIHandler):
        response = await handler.handle_inbox(StubRequest({"username": "nobody"}))

        assert response.status == 200
        body = json.loads(response.text)
        assert body == {"mails": [], "count": 0}

    async def test_limit_is_capped(self, paths: TlonPaths, handler: RESTAPIHandler):
        await _seed_mailbox(paths, "alice", count=5)

        response = await handler.handle_inbox(
            StubRequest({"username": "alice", "limit": "2"})
        )

        body = json.loads(response.text)
        assert body["count"] == 2
        # The most recent mails are kept
        assert [m["content"] for m in body["mails"]] == ["letter 3", "letter 4"]

    async def test_invalid_username_rejected(self, handler: RESTAPIHandler):
        for bad in ("", "../evil", "a/b", "a\\b"):
            response = await handler.handle_inbox(StubRequest({"username": bad}))
            assert response.status == 400, f"username {bad!r} must be rejected"

    async def test_mail_facade_unavailable_returns_503(self):
        handler = RESTAPIHandler(None, mail=None)

        response = await handler.handle_inbox(StubRequest({"username": "alice"}))

        assert response.status == 503

    async def test_read_flag_from_cursor(
        self, paths: TlonPaths, handler: RESTAPIHandler
    ):
        """/inbox exposes read semantics: unread inbox mail is read=false;
        mail marked read (cursor) flips to true."""
        seeded = await _seed_mailbox(paths, "alice", count=2)
        mailbox = Mailbox(paths.member_mail_dir("human:alice"))
        await mailbox.mark_read(seeded[0].id)

        response = await handler.handle_inbox(StubRequest({"username": "alice"}))

        body = json.loads(response.text)
        read_by_id = {m["id"]: m["read"] for m in body["mails"]}
        assert read_by_id[seeded[0].id] is True  # marked read
        assert read_by_id[seeded[1].id] is False  # still unread

    async def test_sent_mail_is_read(
        self, paths: TlonPaths, handler: RESTAPIHandler
    ):
        """The sender's own copy is always read."""
        mailbox = Mailbox(paths.member_mail_dir("human:alice"))
        mail = Mail.create(
            from_address="alice@tlon.internal",
            to=["gu-yanxi@tlon.internal"],
            content="outgoing",
        )
        await mailbox.append_sent(mail)

        response = await handler.handle_inbox(StubRequest({"username": "alice"}))

        body = json.loads(response.text)
        assert body["mails"][0]["read"] is True
