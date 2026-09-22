"""Unit tests for POST /admin/config/agents/{agent_id}/reload (P3 hot adoption).

No HTTP server is started: handle_reload_agent is called directly with a
stub request and a stub registry (wrapped in the members facade), plus one
route-registration check.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.api.members import MembersFacade
from tlon.base.paths import TlonPaths
from tlon_server.api.rest import RESTAPIHandler


class StubRequest:
    """Minimal stand-in for aiohttp.web.Request (path params only)."""

    def __init__(self, agent_id: str):
        self.match_info = {"agent_id": agent_id}


def _make_handler(
    tmp_path: Path, result: str | None = "started"
) -> tuple[RESTAPIHandler, MagicMock]:
    registry = MagicMock()
    registry.adopt_agent = AsyncMock(return_value=result)
    members = MembersFacade(agent_registry=registry, tlon_paths=TlonPaths(home=tmp_path))
    return RESTAPIHandler(None, members=members), registry


class TestHandleReloadAgent:
    """Tests for POST /admin/config/agents/{agent_id}/reload."""

    @pytest.mark.parametrize(
        "result",
        ["started", "registered-lazy", "already-known", "config-not-found", "start-failed"],
    )
    async def test_result_is_passed_through(self, tmp_path: Path, result: str):
        handler, registry = _make_handler(tmp_path, result)

        response = await handler.handle_reload_agent(StubRequest("a-001"))

        assert response.status == 200
        body = json.loads(response.text)
        assert body == {"status": "success", "agent_id": "a-001", "result": result}
        registry.adopt_agent.assert_awaited_once_with("a-001")

    async def test_registry_unavailable_returns_503(self):
        handler = RESTAPIHandler(None)

        response = await handler.handle_reload_agent(StubRequest("a-001"))

        assert response.status == 503
        body = json.loads(response.text)
        assert body["status"] == "error"

    def test_route_registered_with_configs_facade(self, tmp_path: Path):
        handler, _registry = _make_handler(tmp_path)
        handler.configs = MagicMock()

        app = handler.create_app()

        assert any(
            route.method == "POST"
            and route.resource.canonical == "/admin/config/agents/{agent_id}/reload"
            for route in app.router.routes()
        )

    def test_route_absent_without_configs_facade(self, tmp_path: Path):
        handler, _registry = _make_handler(tmp_path)
        handler.configs = None

        app = handler.create_app()

        assert not any(
            route.method == "POST"
            and route.resource.canonical == "/admin/config/agents/{agent_id}/reload"
            for route in app.router.routes()
        )
