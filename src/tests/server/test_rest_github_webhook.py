"""Unit tests for the REST POST /webhooks/github endpoint
(docs/design/mail.md).

No HTTP server is started: handle_github_webhook is called directly with
a stub request (raw body + headers), against a stub GitHubAdapter behind
the events facade.

Covers:
- Valid signature + mapped event: 202 accepted, adapter called
- Valid signature + skipped event: 200 ignored
- Bad or missing signature: 401, adapter never called
- Missing event header / invalid JSON: 400
"""

import hashlib
import hmac
import json
from typing import Any

from tests.identity_helpers import make_identity
from tlon.api.events import EventsFacade
from tlon.base.events.event_bus import EventBus
from tlon.organization.mail.mail import Mail
from tlon_server.api.rest import RESTAPIHandler

SECRET = "test-secret"


class StubRequest:
    """Minimal stand-in for aiohttp.web.Request (raw body + headers)."""

    def __init__(self, body: bytes, headers: dict[str, str] | None = None):
        self._body = body
        self.headers = headers or {}

    async def read(self) -> bytes:
        return self._body


class StubGitHubAdapter:
    """Records receive() calls; returns a fixed mail or None (skip)."""

    def __init__(self, mail: Mail | None):
        self._mail = mail
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def receive(self, event_type: str, payload: dict[str, Any]) -> Mail | None:
        self.calls.append((event_type, payload))
        return self._mail


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _request(
    event_type: str | None,
    payload: Any,
    *,
    signature: str | None = None,
) -> StubRequest:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    headers = {"X-Hub-Signature-256": signature if signature is not None else _sign(body)}
    if event_type is not None:
        headers["X-GitHub-Event"] = event_type
    return StubRequest(body, headers)


def _handler(adapter: StubGitHubAdapter | None) -> RESTAPIHandler:
    events = (
        EventsFacade(event_bus=EventBus(), github_adapter=adapter)  # type: ignore[arg-type]
        if adapter is not None
        else None
    )
    return RESTAPIHandler(
        None, identity=make_identity(), events=events, github_webhook_secret=SECRET
    )


def _accepted_mail() -> Mail:
    return Mail.create(
        from_address="notifications@github.com",
        to=["alice@tlon.internal"],
        subject="[alice/Tlon] 新 issue: t",
        content="body",
    )


class TestSignature:
    async def test_valid_signature_accepted(self):
        adapter = StubGitHubAdapter(_accepted_mail())
        payload = {"action": "opened", "repository": {"full_name": "x/T"}}
        request = _request("issues", payload)

        response = await _handler(adapter).handle_github_webhook(request)  # type: ignore[arg-type]

        assert response.status == 202
        assert json.loads(response.text)["status"] == "accepted"
        assert adapter.calls == [("issues", payload)]

    async def test_wrong_signature_rejected(self):
        adapter = StubGitHubAdapter(_accepted_mail())
        request = _request("issues", {}, signature="sha256=deadbeef")

        response = await _handler(adapter).handle_github_webhook(request)  # type: ignore[arg-type]

        assert response.status == 401
        assert adapter.calls == []

    async def test_missing_signature_header_rejected(self):
        adapter = StubGitHubAdapter(_accepted_mail())
        request = _request("issues", {})
        del request.headers["X-Hub-Signature-256"]

        response = await _handler(adapter).handle_github_webhook(request)  # type: ignore[arg-type]

        assert response.status == 401
        assert adapter.calls == []

    async def test_signature_covers_raw_body(self):
        """The HMAC runs over the raw bytes, not the re-serialized JSON."""
        adapter = StubGitHubAdapter(_accepted_mail())
        body = b'{ "weird" :  "spacing" }'
        request = StubRequest(
            body, {"X-Hub-Signature-256": _sign(body), "X-GitHub-Event": "issues"}
        )

        response = await _handler(adapter).handle_github_webhook(request)  # type: ignore[arg-type]

        assert response.status == 202
        assert adapter.calls == [("issues", {"weird": "spacing"})]


class TestDelivery:
    async def test_ignored_event_returns_200(self):
        adapter = StubGitHubAdapter(None)
        request = _request("push", {"repository": {"full_name": "x/T"}})

        response = await _handler(adapter).handle_github_webhook(request)  # type: ignore[arg-type]

        assert response.status == 200
        assert json.loads(response.text) == {"status": "ignored"}

    async def test_missing_event_header_rejected(self):
        adapter = StubGitHubAdapter(_accepted_mail())
        request = _request(None, {})

        response = await _handler(adapter).handle_github_webhook(request)  # type: ignore[arg-type]

        assert response.status == 400
        assert adapter.calls == []

    async def test_invalid_json_rejected(self):
        adapter = StubGitHubAdapter(_accepted_mail())
        request = _request("issues", b"not json{")

        response = await _handler(adapter).handle_github_webhook(request)  # type: ignore[arg-type]

        assert response.status == 400
        assert adapter.calls == []

    async def test_non_object_json_rejected(self):
        adapter = StubGitHubAdapter(_accepted_mail())
        request = _request("issues", [1, 2, 3])

        response = await _handler(adapter).handle_github_webhook(request)  # type: ignore[arg-type]

        assert response.status == 400
        assert adapter.calls == []

    async def test_unconfigured_returns_503(self):
        """Defensive guard: the route is not registered in this state."""
        request = _request("issues", {})

        response = await _handler(None).handle_github_webhook(request)  # type: ignore[arg-type]

        assert response.status == 503
