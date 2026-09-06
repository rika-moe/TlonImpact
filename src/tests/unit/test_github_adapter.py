"""GitHubAdapter — GitHub webhook events as external incoming mail ().

Pins the v1 event mapping (issues opened/assigned, issue_comment created,
pull_request_review submitted, check_run failure), the addressing rule
(configured recipient → all known humans → skip), the real external
sender address, and the skip-not-raise semantics for unsupported events
and malformed payloads.
"""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from tlon.base.config import AgentConfig, AgentInfoProvider
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail, address_for_member
from tlon.organization.messaging.github_adapter import (
    GITHUB_FROM_ADDRESS,
    GitHubAdapter,
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
) -> GitHubAdapter:
    return GitHubAdapter(
        event_bus=bus,
        agent_info_provider=_AgentInfoStub(agent_ids or []),
        tlon_paths=TlonPaths(home=tmp_path),
        recipient_id=recipient_id,
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
