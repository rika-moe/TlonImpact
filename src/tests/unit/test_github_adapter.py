"""GitHubAdapter — GitHub webhook events as external mail ().

Ingress: pins the v1 event mapping (issues opened/assigned, issue_comment
created, pull_request_review submitted, check_run failure), the
addressing rule (configured recipient → all known humans → skip), the
real external sender address, and the skip-not-raise semantics for
unsupported events and malformed payloads.

Egress: pins the reply → GitHub comment flow — address claim at
initialize, thread mapping (in-memory table + subject anchor fallback),
and the log-only failure semantics (no bounce, no retry, never raise).
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from tlon.base.config import AgentConfig, AgentInfoProvider
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.gateways import ExternalGatewayRegistry
from tlon.organization.mail.mail import Mail, address_for_member
from tlon.organization.messaging.github_adapter import (
    GATEWAY_MEMBER_ID,
    GITHUB_FROM_ADDRESS,
    GitHubAdapter,
    HttpPost,
)

REPO = "alice/Tlon"


class _AgentInfoStub(AgentInfoProvider):
    """Read-only member enumeration for the adapter."""

    def __init__(self, agent_ids: list[str]):
        self._agents = {aid: AgentConfig(id=aid, name=aid) for aid in agent_ids}

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._agents.get(agent_id)

    def get_all_ids(self) -> set[str]:
        return set(self._agents)


@pytest.fixture
async def bus():
    b = EventBus()
    await b.initialize()
    yield b
    await b.shutdown()


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out waiting for condition")


def _make_human(tmp_path: Path, username: str) -> None:
    """A "known human" is a persistent mailbox directory on disk."""
    (tmp_path / f"human-{username}" / "mail").mkdir(parents=True)


def _adapter(
    bus: EventBus,
    tmp_path: Path,
    agent_ids: list[str] | None = None,
    recipient_id: str | None = None,
    registry: ExternalGatewayRegistry | None = None,
    github_token: str | None = None,
    http_post: HttpPost | None = None,
) -> GitHubAdapter:
    return GitHubAdapter(
        event_bus=bus,
        agent_info_provider=_AgentInfoStub(agent_ids or []),
        gateway_registry=registry or ExternalGatewayRegistry(),
        tlon_paths=TlonPaths(home=tmp_path),
        recipient_id=recipient_id,
        github_token=github_token,
        http_post=http_post,
    )


def _collected(bus: EventBus) -> list[Mail]:
    mails: list[Mail] = []

    async def handler(event: Event) -> None:
        if isinstance(event.payload, Mail):
            mails.append(event.payload)

    bus.subscribe(EventType.MAIL_NEW, handler)
    return mails


def _repo() -> dict[str, Any]:
    return {"full_name": REPO}


def _issue(number: int = 376, title: str = "GitHub ingress") -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/{REPO}/issues/{number}",
        "body": "issue body text",
        "user": {"login": "octocat"},
    }


class TestEventMapping:
    async def test_issue_opened(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        adapter = _adapter(bus, tmp_path)
        mails = _collected(bus)

        mail = await adapter.receive(
            "issues", {"action": "opened", "repository": _repo(), "issue": _issue()}
        )

        assert mail is not None
        await _wait_for(lambda: len(mails) == 1)
        assert mail.from_address == GITHUB_FROM_ADDRESS
        assert mail.subject == f"[{REPO}#376] 新 issue: GitHub ingress"
        assert mail.to == [address_for_member("human:alice")]
        assert "octocat" in mail.content
        assert "" in mail.content
        assert "issue body text" in mail.content
        assert "GitHub webhook" in mail.content

    async def test_issue_assigned(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        adapter = _adapter(bus, tmp_path)

        mail = await adapter.receive(
            "issues",
            {
                "action": "assigned",
                "repository": _repo(),
                "issue": _issue(),
                "assignee": {"login": "alice"},
            },
        )

        assert mail is not None
        assert mail.subject == f"[{REPO}#376] issue 指派给 alice: GitHub ingress"

    async def test_issue_comment_on_pr(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        adapter = _adapter(bus, tmp_path)
        issue = _issue(number=12, title="some PR")
        issue["pull_request"] = {"url": "https://api.github.com/..."}

        mail = await adapter.receive(
            "issue_comment",
            {
                "action": "created",
                "repository": _repo(),
                "issue": issue,
                "comment": {
                    "user": {"login": "reviewer"},
                    "html_url": f"https://github.com/{REPO}/pull/12#issuecomment-1",
                    "body": "LGTM",
                },
            },
        )

        assert mail is not None
        assert mail.subject == f"[{REPO}#12] PR 新评论 (reviewer)"
        assert "LGTM" in mail.content

    async def test_pull_request_review_submitted(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        adapter = _adapter(bus, tmp_path)

        mail = await adapter.receive(
            "pull_request_review",
            {
                "action": "submitted",
                "repository": _repo(),
                "pull_request": {"number": 42, "title": "feat: thing"},
                "review": {
                    "state": "approved",
                    "user": {"login": "reviewer"},
                    "html_url": f"https://github.com/{REPO}/pull/42#pullrequestreview-1",
                    "body": "looks good",
                },
            },
        )

        assert mail is not None
        assert mail.subject == f"[{REPO}#42] PR review approved (reviewer)"
        assert "looks good" in mail.content

    async def test_check_run_failure(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        adapter = _adapter(bus, tmp_path)

        mail = await adapter.receive(
            "check_run",
            {
                "action": "completed",
                "repository": _repo(),
                "check_run": {
                    "name": "ci / pytest",
                    "conclusion": "failure",
                    "html_url": f"https://github.com/{REPO}/actions/runs/1",
                    "head_branch": "main",
                    "output": {"summary": "3 tests failed"},
                },
            },
        )

        assert mail is not None
        assert mail.subject == f"[{REPO}] CI 失败: ci / pytest (failure)"
        assert "3 tests failed" in mail.content


class TestSkips:
    async def test_unsupported_event_type(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        adapter = _adapter(bus, tmp_path)
        mails = _collected(bus)

        assert await adapter.receive("push", {"repository": _repo()}) is None
        assert await adapter.receive("ping", {}) is None
        await asyncio.sleep(0.05)
        assert mails == []

    async def test_unsupported_actions(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        adapter = _adapter(bus, tmp_path)
        mails = _collected(bus)

        assert await adapter.receive(
            "issues", {"action": "closed", "repository": _repo(), "issue": _issue()}
        ) is None
        assert await adapter.receive(
            "issue_comment",
            {"action": "edited", "repository": _repo(),
             "issue": _issue(), "comment": {}},
        ) is None
        assert await adapter.receive(
            "pull_request_review",
            {"action": "dismissed", "repository": _repo(),
             "pull_request": {"number": 1}, "review": {}},
        ) is None
        # A successful CI run is not mail-worthy.
        assert await adapter.receive(
            "check_run",
            {"action": "completed", "repository": _repo(),
             "check_run": {"name": "ci", "conclusion": "success"}},
        ) is None
        await asyncio.sleep(0.05)
        assert mails == []

    async def test_bad_payload_does_not_raise(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        adapter = _adapter(bus, tmp_path)
        mails = _collected(bus)

        # Missing repository, missing nested objects, wrong field types —
        # all skipped with a warning, never raised.
        assert await adapter.receive("issues", {"action": "opened"}) is None
        assert await adapter.receive(
            "issues", {"action": "opened", "repository": _repo()}
        ) is None
        assert await adapter.receive(
            "issues",
            {"action": "opened", "repository": _repo(),
             "issue": {"title": "no number"}},
        ) is None
        assert await adapter.receive(
            "issue_comment",
            {"action": "created", "repository": _repo(), "issue": _issue()},
        ) is None
        await asyncio.sleep(0.05)
        assert mails == []


class TestAddressing:
    async def test_configured_recipient(self, bus, tmp_path):
        """A configured agent recipient receives the mail, not the humans."""
        _make_human(tmp_path, "alice")
        adapter = _adapter(bus, tmp_path, agent_ids=["gu-yanxi"],
                           recipient_id="gu-yanxi")

        mail = await adapter.receive(
            "issues", {"action": "opened", "repository": _repo(), "issue": _issue()}
        )

        assert mail is not None
        assert mail.to == [address_for_member("gu-yanxi")]

    async def test_configured_human_recipient(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        _make_human(tmp_path, "bob")
        adapter = _adapter(bus, tmp_path, recipient_id="human:alice")

        mail = await adapter.receive(
            "issues", {"action": "opened", "repository": _repo(), "issue": _issue()}
        )

        assert mail is not None
        assert mail.to == [address_for_member("human:alice")]

    async def test_all_known_humans_when_unconfigured(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        _make_human(tmp_path, "bob")
        adapter = _adapter(bus, tmp_path)

        mail = await adapter.receive(
            "issues", {"action": "opened", "repository": _repo(), "issue": _issue()}
        )

        assert mail is not None
        assert mail.to == [
            address_for_member("human:alice"),
            address_for_member("human:bob"),
        ]

    async def test_unknown_configured_recipient_skips(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        adapter = _adapter(bus, tmp_path, recipient_id="ghost")
        mails = _collected(bus)

        assert await adapter.receive(
            "issues", {"action": "opened", "repository": _repo(), "issue": _issue()}
        ) is None
        await asyncio.sleep(0.05)
        assert mails == []

    async def test_no_humans_no_recipient_skips(self, bus, tmp_path):
        adapter = _adapter(bus, tmp_path)
        mails = _collected(bus)

        assert await adapter.receive(
            "issues", {"action": "opened", "repository": _repo(), "issue": _issue()}
        ) is None
        await asyncio.sleep(0.05)
        assert mails == []


class _FakePost:
    """Fake egress transport: records calls, returns a fixed status."""

    def __init__(self, status: int = 201, error: Exception | None = None):
        self.status = status
        self.error = error
        self.calls: list[tuple[str, dict[str, str], dict[str, Any]]] = []

    async def __call__(
        self, url: str, headers: dict[str, str], body: dict[str, Any]
    ) -> int:
        self.calls.append((url, headers, body))
        if self.error is not None:
            raise self.error
        return self.status


def _reply(inbound: Mail | None, subject: str | None, content: str = "收到，我来处理") -> Mail:
    return Mail.create(
        from_address="alice@tlon.internal",
        to=[GITHUB_FROM_ADDRESS],
        subject=subject,
        content=content,
        in_reply_to=inbound.id if inbound else None,
        thread_id=inbound.thread_id if inbound else None,
    )


def _delivered_event(mail: Mail, recipient: str = GATEWAY_MEMBER_ID) -> Event:
    return Event(
        type=EventType.MAIL_DELIVERED,
        payload={"mail": mail, "recipient": recipient},
        source="mail_delivery",
    )


class TestEgress:
    """Reply → GitHub comment (issue , outbound direction)."""

    async def _inbound(self, bus: EventBus, adapter: GitHubAdapter) -> Mail:
        mail = await adapter.receive(
            "issues", {"action": "opened", "repository": _repo(), "issue": _issue()}
        )
        assert mail is not None
        return mail

    async def test_initialize_claims_the_gateway_address(self, bus, tmp_path):
        registry = ExternalGatewayRegistry()
        adapter = _adapter(bus, tmp_path, registry=registry)
        await adapter.initialize()
        assert registry.owner_of(GITHUB_FROM_ADDRESS) == GATEWAY_MEMBER_ID
        await adapter.shutdown()

    async def test_reply_posts_comment_via_thread_table(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        post = _FakePost()
        adapter = _adapter(bus, tmp_path, github_token="tok", http_post=post)
        await adapter.initialize()
        inbound = await self._inbound(bus, adapter)

        await bus.publish(_delivered_event(_reply(inbound, inbound.subject)))

        await _wait_for(lambda: len(post.calls) == 1)
        url, headers, body = post.calls[0]
        assert url == f"https://api.github.com/repos/{REPO}/issues/376/comments"
        assert headers["Authorization"] == "Bearer tok"
        assert headers["Accept"] == "application/vnd.github+json"
        assert body == {"body": "收到，我来处理"}
        await adapter.shutdown()

    async def test_subject_anchor_fallback(self, bus, tmp_path):
        """No thread-table entry (e.g. after a restart): the subject's
        [owner/repo#N] anchor still maps the reply to its GitHub object."""
        post = _FakePost()
        adapter = _adapter(bus, tmp_path, github_token="tok", http_post=post)
        await adapter.initialize()

        reply = _reply(None, f"[{REPO}#99] Re: 旧线程")
        reply.in_reply_to = "unknown-mail-id"
        await bus.publish(_delivered_event(reply))

        await _wait_for(lambda: len(post.calls) == 1)
        url, _, _ = post.calls[0]
        assert url == f"https://api.github.com/repos/{REPO}/issues/99/comments"
        await adapter.shutdown()

    async def test_unmapped_reply_only_logged(self, bus, tmp_path):
        """Neither table hit nor anchor: log and keep the mail in the
        gateway mailbox (audit) — no post, no raise, no retry."""
        post = _FakePost()
        adapter = _adapter(bus, tmp_path, github_token="tok", http_post=post)
        await adapter.initialize()

        reply = _reply(None, "没有锚点的信")
        await bus.publish(_delivered_event(reply))

        await asyncio.sleep(0.1)
        assert post.calls == []
        await adapter.shutdown()

    async def test_missing_token_skips_egress(self, bus, tmp_path):
        """No GITHUB_TOKEN: egress disabled, ingress unaffected."""
        _make_human(tmp_path, "alice")
        post = _FakePost()
        adapter = _adapter(bus, tmp_path, github_token=None, http_post=post)
        await adapter.initialize()
        inbound = await self._inbound(bus, adapter)  # ingress still works

        await bus.publish(_delivered_event(_reply(inbound, inbound.subject)))

        await asyncio.sleep(0.1)
        assert post.calls == []
        await adapter.shutdown()

    async def test_other_recipients_delivery_ignored(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        post = _FakePost()
        adapter = _adapter(bus, tmp_path, github_token="tok", http_post=post)
        await adapter.initialize()
        inbound = await self._inbound(bus, adapter)

        await bus.publish(
            _delivered_event(_reply(inbound, inbound.subject), recipient="a-001")
        )

        await asyncio.sleep(0.1)
        assert post.calls == []
        await adapter.shutdown()

    async def test_non_2xx_logged_no_raise(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        post = _FakePost(status=403)
        adapter = _adapter(bus, tmp_path, github_token="tok", http_post=post)
        await adapter.initialize()
        inbound = await self._inbound(bus, adapter)

        await bus.publish(_delivered_event(_reply(inbound, inbound.subject)))

        await _wait_for(lambda: len(post.calls) == 1)  # attempted, logged
        await adapter.shutdown()

    async def test_http_error_logged_no_raise(self, bus, tmp_path):
        _make_human(tmp_path, "alice")
        post = _FakePost(error=ConnectionError("network down"))
        adapter = _adapter(bus, tmp_path, github_token="tok", http_post=post)
        await adapter.initialize()
        inbound = await self._inbound(bus, adapter)

        await bus.publish(_delivered_event(_reply(inbound, inbound.subject)))

        await _wait_for(lambda: len(post.calls) == 1)  # attempted, logged
        await adapter.shutdown()
