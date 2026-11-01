"""Project governance shares the existing local human browser boundary."""

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tlon.api.gitea import GiteaFacade
from tlon.api.gitea_projects import GiteaProjectError
from tlon_server.api.rest import RESTAPIHandler
from tlon_server.core.local_identity import LocalIdentity

WEBUI = {"X-Tlon-WebUI": "1"}
ROUTES = [
    ("GET", "/admin/gitea/projects"),
    ("POST", "/admin/gitea/projects"),
    ("PUT", "/admin/gitea/projects/example/reviewers/stable-id"),
]


@pytest.fixture
async def browser(tmp_path: Path) -> AsyncIterator[tuple[TestClient, MagicMock]]:
    projects = MagicMock()
    projects.list_projects = AsyncMock(return_value={"projects": [], "members": []})
    projects.configure = AsyncMock(return_value={"name": "example", "managed": True})
    projects.set_reviewer = AsyncMock(return_value={"reviewers": ["bound-user"]})
    gitea = cast(GiteaFacade, SimpleNamespace(projects=projects))
    handler = RESTAPIHandler(None, identity=LocalIdentity(tmp_path / "identity.json"), gitea=gitea)
    async with TestClient(TestServer(handler.create_app())) as client:
        yield client, projects


async def initialize(client: TestClient) -> None:
    response = await client.post("/identity", headers=WEBUI, json={"username": "operator"})
    assert response.status == 201


@pytest.mark.parametrize("method,path", ROUTES)
async def test_project_routes_require_initialized_identity(
    browser: tuple[TestClient, MagicMock], method: str, path: str
) -> None:
    client, projects = browser
    response = await client.request(method, path, headers=WEBUI, json={})
    assert response.status == 409
    assert (await response.json())["reason"] == "identity_required"
    assert not projects.mock_calls


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize(
    "headers",
    [{}, {**WEBUI, "Origin": "https://evil.example"}, {**WEBUI, "Sec-Fetch-Site": "cross-site"}],
)
async def test_project_routes_reject_untrusted_browser(
    browser: tuple[TestClient, MagicMock], method: str, path: str, headers: dict[str, str]
) -> None:
    client, projects = browser
    await initialize(client)
    response = await client.request(method, path, headers=headers, json={})
    assert response.status == 403
    assert not projects.mock_calls
    assert not any(key.lower().startswith("access-control-") for key in response.headers)


async def test_project_routes_forward_exact_operations(
    browser: tuple[TestClient, MagicMock],
) -> None:
    client, projects = browser
    await initialize(client)
    headers = {**WEBUI, "Origin": str(client.make_url("/")).rstrip("/")}
    response = await client.get("/admin/gitea/projects", headers=headers)
    assert response.status == 200 and await response.json() == {"projects": [], "members": []}
    body = {
        "name": "example",
        "create": True,
        "default_branch": "main",
        "protected_branches": ["release/**"],
    }
    response = await client.post("/admin/gitea/projects", headers=headers, json=body)
    assert response.status == 200 and (await response.json())["managed"]
    projects.configure.assert_awaited_once_with(body)
    response = await client.put(
        "/admin/gitea/projects/example/reviewers/stable-id",
        headers=headers,
        json={"enabled": False},
    )
    assert response.status == 200
    projects.set_reviewer.assert_awaited_once_with("example", "stable-id", {"enabled": False})


@pytest.mark.parametrize("method,path", ROUTES[1:])
async def test_malformed_json_does_not_reach_coordinator(
    browser: tuple[TestClient, MagicMock], method: str, path: str
) -> None:
    client, projects = browser
    await initialize(client)
    response = await client.request(
        method, path, headers={**WEBUI, "Content-Type": "application/json"}, data="{"
    )
    assert response.status == 400
    assert not projects.mock_calls


@pytest.mark.parametrize("method,path", ROUTES)
@pytest.mark.parametrize("status", [400, 409, 503])
async def test_safe_project_errors_preserve_http_status(
    browser: tuple[TestClient, MagicMock], method: str, path: str, status: int
) -> None:
    client, projects = browser
    await initialize(client)
    for operation in (projects.list_projects, projects.configure, projects.set_reviewer):
        operation.side_effect = GiteaProjectError("safe diagnostic", status=status)
    response = await client.request(method, path, headers=WEBUI, json={})
    assert response.status == status
    assert await response.json() == {"error": "safe diagnostic"}
