"""Real HTTP regressions for malicious websites reaching the local WebUI API."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer
from multidict import CIMultiDictProxy

from tlon_server.api.rest import RESTAPIHandler
from tlon_server.core.local_identity import LocalIdentity

WEBUI = {"X-Tlon-WebUI": "1"}


@pytest.fixture
async def browser(tmp_path: Path) -> AsyncIterator[TestClient]:
    handler = RESTAPIHandler(None, identity=LocalIdentity(tmp_path / "identity.json"))
    async with TestClient(TestServer(handler.create_app())) as client:
        yield client


def assert_no_cors(headers: CIMultiDictProxy[str]) -> None:
    assert not any(str(key).lower().startswith("access-control-") for key in headers)


async def test_initialization_then_another_browser_uses_same_identity(browser: TestClient) -> None:
    response = await browser.get("/identity", headers=WEBUI)
    assert response.status == 200
    assert await response.json() == {"username": None, "address": None}
    response = await browser.get("/inbox", headers=WEBUI)
    assert response.status == 409
    assert (await response.json())["reason"] == "identity_required"
    response = await browser.post("/identity", headers=WEBUI, json={"username": "xorsum"})
    assert response.status == 201
    # No cookies or browser-local storage are required to recover the address.
    async with ClientSession() as second:
        response = await second.get(browser.make_url("/identity"), headers=WEBUI)
        assert await response.json() == {"username": "xorsum", "address": "xorsum@tlon.internal"}
    response = await browser.post("/identity", headers=WEBUI, json={"username": "bob"})
    assert response.status == 409


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Tlon-WebUI": "0"},
        {**WEBUI, "Origin": "https://evil.example"},
        {**WEBUI, "Origin": "null"},
        {**WEBUI, "Host": "evil.example"},
        {**WEBUI, "Host": "127.0.0.1:1"},
        {**WEBUI, "Sec-Fetch-Site": "cross-site"},
        {**WEBUI, "Sec-Fetch-Site": "same-site"},
    ],
)
@pytest.mark.parametrize("path", ["/identity", "/inbox", "/admin/agents", "/events"])
async def test_untrusted_browser_requests_are_rejected(
    browser: TestClient, headers: dict[str, str], path: str
) -> None:
    response = await browser.get(path, headers=headers)
    assert response.status == 403
    assert_no_cors(response.headers)


async def test_cross_origin_preflight_cannot_grant_custom_header(browser: TestClient) -> None:
    response = await browser.options(
        "/identity",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Tlon-WebUI,Content-Type",
        },
    )
    assert response.status == 403
    assert_no_cors(response.headers)


async def test_matching_origin_is_allowed_but_not_a_substitute_for_request_header(
    browser: TestClient,
) -> None:
    origin = str(browser.make_url("/")).rstrip("/")
    response = await browser.get("/identity", headers={**WEBUI, "Origin": origin})
    assert response.status == 200
    assert_no_cors(response.headers)
    response = await browser.get("/identity", headers={"Origin": origin})
    assert response.status == 403


async def test_native_eventsource_same_origin_exception_is_only_for_events(
    browser: TestClient,
) -> None:
    created = await browser.post("/identity", headers=WEBUI, json={"username": "alice"})
    assert created.status == 201
    response = await browser.get("/events", headers={"Sec-Fetch-Site": "same-origin"})
    assert response.status == 404  # No gateway session, but passed the browser boundary.
    assert_no_cors(response.headers)
    response = await browser.get("/inbox", headers={"Sec-Fetch-Site": "same-origin"})
    assert response.status == 403


async def test_static_and_health_are_public_but_never_cross_origin_readable(
    browser: TestClient,
) -> None:
    response = await browser.get("/health")
    assert response.status == 200
    assert_no_cors(response.headers)
    response = await browser.get("/ui/")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert_no_cors(response.headers)


@pytest.mark.parametrize("username", [None, "", "../evil", "postmaster", "adam", "a" * 65])
async def test_invalid_initialization_leaves_instance_unset(
    browser: TestClient, username: object
) -> None:
    response = await browser.post("/identity", headers=WEBUI, json={"username": username})
    assert response.status == 400
    assert (await response.json())["reason"] == "invalid_username"
    response = await browser.get("/identity", headers=WEBUI)
    assert (await response.json())["username"] is None


async def test_existing_agent_address_cannot_be_claimed(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    members = MagicMock()
    members.list_ids.return_value = ["existing-agent"]
    identity = LocalIdentity(tmp_path / "identity.json")
    handler = RESTAPIHandler(None, identity=identity, members=members)
    async with TestClient(TestServer(handler.create_app())) as client:
        response = await client.post(
            "/identity", headers=WEBUI, json={"username": "EXISTING-agent"}
        )
        assert response.status == 409
        assert (await response.json())["reason"] == "identity_conflict"
        assert identity.username is None
        response = await client.post("/identity", headers=WEBUI, json={"username": "alice"})
        assert response.status == 201
