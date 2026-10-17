"""Unit tests for the REST GET /members/{member_id}/mail endpoint.

No HTTP server is started: handle_member_mail is called directly with a
stub request, against a MailFacade rooted at a tmp dir.

Covers:
- Agent and human member mailboxes (inbox + sent merged, chronological)
- Observer semantics: no "read" field synthesized, no mailbox mkdir
- Unknown member -> 404; known member with empty mailbox -> 200 []
- limit parameter; invalid member id -> 400; facade missing -> 503
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.identity_helpers import make_identity
from tlon.api.mail import MailFacade
from tlon.base.config import AgentConfig
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.store import MailStore
from tlon_server.api.rest import RESTAPIHandler


class StubRequest:
    """Minimal stand-in for aiohttp.web.Request (match_info + query)."""

    def __init__(self, member_id: str, query: dict[str, str] | None = None):
        self.match_info = {"member_id": member_id}
        self.query = query or {}


class StubAgentRegistry:
    """Existence lookup for a fixed set of agent ids."""

    def __init__(self, agent_ids: list[str]):
        self._configs = {
            agent_id: AgentConfig(id=agent_id, name=agent_id) for agent_id in agent_ids
        }

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)


@pytest.fixture
def paths(tmp_path: Path) -> TlonPaths:
    return TlonPaths(home=tmp_path)


@pytest.fixture
def handler(paths: TlonPaths) -> RESTAPIHandler:
    return RESTAPIHandler(
        None,
        identity=make_identity(),
        mail=MailFacade(
            adapter_manager=MagicMock(),
            agent_registry=StubAgentRegistry(["gu-yanxi"]),  # type: ignore[arg-type]
            tlon_paths=paths,
        ),
    )


async def _seed_mailbox(paths: TlonPaths, member_id: str, count: int = 3) -> list[Mail]:
    """Write a few mails into the member's inbox and return them."""
    mailbox = Mailbox(paths.mail_database_file(), member_id)
    mails = [
        Mail.create(
            from_address="postmaster@tlon.internal",
            to=[f"{member_id.removeprefix('human:')}@tlon.internal"],
            content=f"letter {i}",
        )
        for i in range(count)
    ]
    MailStore(paths.mail_database_file()).register_member(mailbox.member_id)
    for mail in mails:
        await mailbox.append_inbox(mail)
    return mails


class TestHandleMemberMail:
    """Tests for GET /members/{member_id}/mail."""

    async def test_reads_agent_mailbox(self, paths: TlonPaths, handler: RESTAPIHandler):
        seeded = await _seed_mailbox(paths, "gu-yanxi")

        response = await handler.handle_member_mail(StubRequest("gu-yanxi"))

        assert response.status == 200
        body = json.loads(response.text)
        assert body["count"] == len(seeded)
        assert [m["id"] for m in body["mails"]] == [m.id for m in seeded]

    async def test_reads_human_mailbox(self, paths: TlonPaths, handler: RESTAPIHandler):
        seeded = await _seed_mailbox(paths, "human:alice")

        response = await handler.handle_member_mail(StubRequest("human:alice"))

        assert response.status == 200
        body = json.loads(response.text)
        assert body["count"] == len(seeded)
        assert [m["id"] for m in body["mails"]] == [m.id for m in seeded]

    async def test_no_read_field_synthesized(self, paths: TlonPaths, handler: RESTAPIHandler):
        """Observer view must not expose the member's read cursor."""
        seeded = await _seed_mailbox(paths, "gu-yanxi", count=2)
        mailbox = Mailbox(paths.mail_database_file(), "gu-yanxi")
        await mailbox.mark_read(seeded[0].id)

        response = await handler.handle_member_mail(StubRequest("gu-yanxi"))

        body = json.loads(response.text)
        assert all("read" not in m for m in body["mails"])

    async def test_unknown_agent_returns_404(self, handler: RESTAPIHandler):
        response = await handler.handle_member_mail(StubRequest("nobody"))

        assert response.status == 404
        body = json.loads(response.text)
        assert body["error"]

    async def test_unknown_human_returns_404(self, paths: TlonPaths, handler: RESTAPIHandler):
        """A human without a persistent mailbox is not a known member —
        and the lookup must not materialize one."""
        response = await handler.handle_member_mail(StubRequest("human:nobody"))

        assert response.status == 404
        assert not paths.member_mail_dir("human:nobody").exists()

    async def test_empty_mailbox_returns_200(self, handler: RESTAPIHandler):
        """Known member, no mail yet: 200 with an empty list (no mkdir)."""
        response = await handler.handle_member_mail(StubRequest("gu-yanxi"))

        assert response.status == 200
        body = json.loads(response.text)
        assert body == {"mails": [], "count": 0, "has_more": False, "next_before": None}

    async def test_limit_is_capped(self, paths: TlonPaths, handler: RESTAPIHandler):
        await _seed_mailbox(paths, "gu-yanxi", count=5)

        response = await handler.handle_member_mail(StubRequest("gu-yanxi", {"limit": "2"}))

        body = json.loads(response.text)
        assert body["count"] == 2
        # The most recent mails are kept
        assert [m["content"] for m in body["mails"]] == ["letter 3", "letter 4"]

    async def test_invalid_member_id_rejected(self, handler: RESTAPIHandler):
        for bad in ("", "  ", "../evil", "a/b", "a\\b"):
            response = await handler.handle_member_mail(StubRequest(bad))
            assert response.status == 400, f"member_id {bad!r} must be rejected"

    async def test_mail_facade_unavailable_returns_503(self):
        handler = RESTAPIHandler(None, identity=make_identity(), mail=None)

        response = await handler.handle_member_mail(StubRequest("gu-yanxi"))

        assert response.status == 503
