"""Admin REST API — agent CRUD + prototype approval (wizard removal, A2).

No HTTP server is started: handlers are called directly with stub requests
and a stub registry, against a real temp TlonPaths so instantiate / persona
writes / staging approval exercise the real paths.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from tlon.agents.prototypes import (
    get_prototype_info,
    seed_prototypes,
    write_staging_prototype,
)
from tlon.app.server.api.rest import RESTAPIHandler
from tlon.base.config import yaml_load_all
from tlon.base.paths import TlonPaths
from tlon.tools.agents.ports import StopRejectedError


class StubRequest:
    """Minimal stand-in for aiohttp.web.Request."""

    def __init__(self, body: dict | None = None, agent_id: str | None = None, name: str | None = None):
        self._body = body or {}
        self.match_info = {}
        if agent_id is not None:
            self.match_info["agent_id"] = agent_id
        if name is not None:
            self.match_info["name"] = name

    async def json(self) -> dict:
        return self._body


def _handler(tmp_path: Path, registry: MagicMock | None = None) -> tuple[RESTAPIHandler, MagicMock, TlonPaths]:
    p = TlonPaths(home=tmp_path)
    seed_prototypes(p)
    reg = registry
    if reg is None:
        reg = MagicMock()
        reg.adopt_agent = AsyncMock(return_value="started")
        reg.remove_member = AsyncMock(return_value=True)
    handler = RESTAPIHandler(None, reg, paths=p)
    return handler, reg, p


class TestAdminCreateAgent:
    async def test_create_from_prototype_with_persona(self, tmp_path: Path):
        handler, reg, p = _handler(tmp_path)
        req = StubRequest(
            body={
                "prototype": "developer",
                "name": "新工程师",
                "agent_id": "dev-1",
                "persona": {"identity": "你是新工程师", "profile": "开发", "behavior": "干活"},
            }
        )

        resp = await handler.handle_create_agent(req)

        assert resp.status == 200
        body = json.loads(resp.text)
        assert body["agent_id"] == "dev-1"
        assert body["prototype"] == "developer"
        reg.adopt_agent.assert_awaited_once_with("dev-1")

        configs = yaml_load_all(p.tlon_home())
        assert configs["dev-1"].prototype == "developer"
        behavior = p.agent_behavior_file("dev-1")
        assert behavior.exists() and "${" not in behavior.read_text(encoding="utf-8")
        identity = p.agent_config_dir("dev-1") / "IDENTITY.md"
        assert identity.read_text(encoding="utf-8").strip() == "你是新工程师"

    async def test_missing_fields_rejected(self, tmp_path: Path):
        handler, _reg, _p = _handler(tmp_path)

        resp = await handler.handle_create_agent(StubRequest(body={"prototype": "developer"}))

        assert resp.status == 400

    async def test_unknown_prototype_rejected(self, tmp_path: Path):
        handler, _reg, _p = _handler(tmp_path)

        resp = await handler.handle_create_agent(
            StubRequest(body={"prototype": "nope", "name": "x"})
        )

        assert resp.status == 400

    def test_route_registered(self, tmp_path: Path):
        handler, _reg, _p = _handler(tmp_path)
        app = handler.create_app()
        assert any(
            route.method == "POST"
            and route.resource.canonical == "/admin/config/agents"
            for route in app.router.routes()
        )


class TestAdminUpdateAgent:
    async def test_update_persona_and_llm(self, tmp_path: Path):
        handler, reg, p = _handler(tmp_path)
        from tlon.agents.prototypes import instantiate

        instantiate("developer", "dev-1", "新工程师", tlon_paths=p)
        req = StubRequest(
            body={
                "persona": {"identity": "新身份"},
                "llm": {"model_name": "other-model", "temperature": 0.7},
            },
            agent_id="dev-1",
        )

        resp = await handler.handle_update_agent(req)

        assert resp.status == 200
        identity = p.agent_config_dir("dev-1") / "IDENTITY.md"
        assert identity.read_text(encoding="utf-8").strip() == "新身份"
        cfg = yaml_load_all(p.tlon_home())["dev-1"]
        assert cfg.llm.model_name == "other-model"
        assert cfg.llm.temperature == 0.7
        reg.adopt_agent.assert_awaited_once_with("dev-1")

    async def test_unknown_agent_404(self, tmp_path: Path):
        handler, _reg, _p = _handler(tmp_path)

        resp = await handler.handle_update_agent(
            StubRequest(body={}, agent_id="nobody")
        )

        assert resp.status == 404


class TestAdminDeleteAgent:
    async def test_delete_calls_remove_member(self, tmp_path: Path):
        handler, reg, _p = _handler(tmp_path)

        resp = await handler.handle_delete_agent(StubRequest(agent_id="dev-1"))

        assert resp.status == 200
        reg.remove_member.assert_awaited_once_with("dev-1", removed_by="admin-api")

    async def test_live_sessions_conflict(self, tmp_path: Path):
        reg = MagicMock()
        reg.remove_member = AsyncMock(
            side_effect=StopRejectedError("active_sessions", "busy")
        )
        handler, _reg, _p = _handler(tmp_path, reg)

        resp = await handler.handle_delete_agent(StubRequest(agent_id="dev-1"))

        assert resp.status == 409
        assert json.loads(resp.text)["reason"] == "active_sessions"


class TestAdminStaging:
    async def _staged(self, tmp_path: Path) -> TlonPaths:
        p = TlonPaths(home=tmp_path)
        seed_prototypes(p)
        write_staging_prototype(
            "llm-idea",
            "{:version 1 :rules [{:name \"r\" :on :boot :actions []}]}\n",
            display_name="LLM 创意",
            description="由 agent 创作",
            author_id="gu-yanxi",
            tlon_paths=p,
        )
        return p

    async def test_list_approve_reject(self, tmp_path: Path):
        p = await self._staged(tmp_path)
        handler = RESTAPIHandler(None, MagicMock(), paths=p)

        listed = await handler.handle_list_staging(StubRequest())
        assert listed.status == 200
        names = [x["name"] for x in json.loads(listed.text)["prototypes"]]
        assert names == ["llm-idea"]

        approved = await handler.handle_approve_prototype(StubRequest(name="llm-idea"))
        assert approved.status == 200
        info = get_prototype_info("llm-idea", tlon_paths=p)
        assert info is not None and info.trusted

        # Staging list is now empty; approving again fails.
        listed2 = await handler.handle_list_staging(StubRequest())
        assert json.loads(listed2.text)["prototypes"] == []

    async def test_reject_removes_staging(self, tmp_path: Path):
        p = await self._staged(tmp_path)
        handler = RESTAPIHandler(None, MagicMock(), paths=p)

        rejected = await handler.handle_reject_prototype(StubRequest(name="llm-idea"))
        assert rejected.status == 200
        assert get_prototype_info("llm-idea", tlon_paths=p) is None
        assert not (p.prototypes_staging_dir() / "llm-idea").exists()
