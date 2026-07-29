"""Unit tests for the REST /inbox endpoint (mail model, R2).

No HTTP server is started: handle_inbox is called directly with a stub
request, against a TlonPaths rooted at a tmp dir.
"""

import json
from pathlib import Path

import pytest

from tlon.app.server.api.rest import RESTAPIHandler
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox


class StubRequest:
    """Minimal stand-in for aiohttp.web.Request (query access only)."""

    def __init__(self, query: dict[str, str] | None = None):
        self.query = query or {}


@pytest.fixture
def paths(tmp_path: Path) -> TlonPaths:
    return TlonPaths(home=tmp_path)


@pytest.fixture
def handler(paths: TlonPaths) -> RESTAPIHandler:
    return RESTAPIHandler(None, None, paths=paths)


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

    async def test_paths_unavailable_returns_503(self):
        handler = RESTAPIHandler(None, None, paths=None)

        response = await handler.handle_inbox(StubRequest({"username": "alice"}))

        assert response.status == 503
