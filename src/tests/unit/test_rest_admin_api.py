"""Admin REST API — agent CRUD + prototype approval (wizard removal, A2).

No HTTP server is started: handlers are called directly with stub requests
and a stub registry, against a real temp TlonPaths so instantiate / persona
writes / staging approval exercise the real paths.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from tlon.agents.prototypes import (
    append_staging_review,
    get_prototype_detail,
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
                "model": "deepseek/deepseek-chat",
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
        # member-level LLM config (ADR 0016): the model lands in agent.yaml
        assert configs["dev-1"].llm is not None
        assert configs["dev-1"].llm.model_name == "deepseek/deepseek-chat"
        behavior = p.agent_behavior_file("dev-1")
        assert behavior.exists() and "${" not in behavior.read_text(encoding="utf-8")
        identity = p.agent_config_dir("dev-1") / "IDENTITY.md"
        assert identity.read_text(encoding="utf-8").strip() == "你是新工程师"

    async def test_missing_fields_rejected(self, tmp_path: Path):
        handler, _reg, _p = _handler(tmp_path)

        resp = await handler.handle_create_agent(StubRequest(body={"prototype": "developer"}))

        assert resp.status == 400

    async def test_missing_model_rejected(self, tmp_path: Path):
        """ADR 0016: model is required — no process-level fallback."""
        handler, _reg, _p = _handler(tmp_path)

        resp = await handler.handle_create_agent(
            StubRequest(body={"prototype": "developer", "name": "x"})
        )

        assert resp.status == 400
        assert json.loads(resp.text)["message"] == "model is required"

    async def test_unknown_prototype_rejected(self, tmp_path: Path):
        handler, _reg, _p = _handler(tmp_path)

        resp = await handler.handle_create_agent(
            StubRequest(body={"prototype": "nope", "name": "x", "model": "deepseek/deepseek-chat"})
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

        instantiate("developer", "dev-1", "新工程师", model="openai/gpt-5", tlon_paths=p)
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


class TestAdminPrototypeManagement:
    """Prototype management endpoints (WebUI PROTOTYPES view): detail,
    human-authored create, edit, delete."""

    _BEHAVIOR = (
        "{:version 1 :rules [{:name \"r\" :on :boot :actions []}]}\n"
    )
    _TEMPLATED = (
        "{:version 1\n"
        " :parameters {:to {:type :string-list :required true}}\n"
        " :rules [{:name \"relay\" :on :mail-delivered"
        " :actions [(forward {:to ${to}})]}]}\n"
    )
    _INVALID = (
        "{:version 1 :rules [{:name \"w\" :on :mail-delivered"
        " :actions [(jump {:to [\"x@tlon.internal\"]})]}]}\n"
    )

    def _handler(self, tmp_path: Path) -> tuple[RESTAPIHandler, TlonPaths]:
        p = TlonPaths(home=tmp_path)
        return RESTAPIHandler(None, MagicMock(), paths=p), p

    async def test_create_writes_trusted_library(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)

        resp = await handler.handle_create_prototype(
            StubRequest(body={"name": "hand", "behavior": self._BEHAVIOR,
                              "display_name": "手写"})
        )

        assert resp.status == 201
        proto = json.loads(resp.text)["prototype"]
        assert proto["trusted"] is True
        assert proto["author"] is None
        assert proto["parameters"] == []
        info = get_prototype_info("hand", tlon_paths=p)
        assert info is not None and info.trusted

    async def test_create_missing_fields_400(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)

        resp = await handler.handle_create_prototype(StubRequest(body={"name": "x"}))

        assert resp.status == 400
        assert json.loads(resp.text)["reason"] == "invalid_prototype"

    async def test_create_invalid_behavior_400_nothing_on_disk(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)

        resp = await handler.handle_create_prototype(
            StubRequest(body={"name": "bad", "behavior": self._INVALID})
        )

        assert resp.status == 400
        assert json.loads(resp.text)["reason"] == "invalid_prototype"
        assert not (p.prototypes_dir() / "bad").exists()

    async def test_detail_includes_behavior_and_parameters(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        await handler.handle_create_prototype(
            StubRequest(body={"name": "tpl", "behavior": self._TEMPLATED})
        )

        resp = await handler.handle_get_prototype(StubRequest(name="tpl"))

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["behavior"] == self._TEMPLATED
        assert proto["parameters"] == [
            {"name": "to", "type": "string-list", "required": True}
        ]

    async def test_detail_unknown_404(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)

        resp = await handler.handle_get_prototype(StubRequest(name="nope"))

        assert resp.status == 404
        assert json.loads(resp.text)["reason"] == "prototype_not_found"

    async def test_staging_detail_is_untrusted(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        write_staging_prototype("staged", self._BEHAVIOR, author_id="adam", tlon_paths=p)

        resp = await handler.handle_get_staging_prototype(StubRequest(name="staged"))

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["trusted"] is False
        assert proto["author"] == "adam"

    async def test_update_metadata_and_behavior(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        await handler.handle_create_prototype(
            StubRequest(body={"name": "hand", "behavior": self._BEHAVIOR})
        )

        resp = await handler.handle_update_prototype(
            StubRequest(name="hand",
                        body={"description": "新描述", "behavior": self._TEMPLATED})
        )

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["description"] == "新描述"
        assert proto["trusted"] is True
        _info, behavior, _, _reviews = get_prototype_detail("hand", tlon_paths=p)
        assert behavior == self._TEMPLATED

    async def test_update_empty_body_400(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        await handler.handle_create_prototype(
            StubRequest(body={"name": "hand", "behavior": self._BEHAVIOR})
        )

        resp = await handler.handle_update_prototype(StubRequest(name="hand", body={}))

        assert resp.status == 400
        assert json.loads(resp.text)["reason"] == "invalid_prototype"

    async def test_update_invalid_behavior_leaves_disk_untouched(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        await handler.handle_create_prototype(
            StubRequest(body={"name": "hand", "behavior": self._BEHAVIOR})
        )

        resp = await handler.handle_update_prototype(
            StubRequest(name="hand", body={"behavior": self._INVALID})
        )

        assert resp.status == 400
        assert json.loads(resp.text)["reason"] == "invalid_prototype"
        _info, behavior, _, _reviews = get_prototype_detail("hand", tlon_paths=p)
        assert behavior == self._BEHAVIOR

    async def test_update_unknown_404(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)

        resp = await handler.handle_update_prototype(
            StubRequest(name="nope", body={"description": "d"})
        )

        assert resp.status == 404
        assert json.loads(resp.text)["reason"] == "prototype_not_found"

    async def test_update_staging_preserves_author(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        write_staging_prototype("staged", self._BEHAVIOR, author_id="adam", tlon_paths=p)

        resp = await handler.handle_update_staging_prototype(
            StubRequest(name="staged", body={"display_name": "改名"})
        )

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["trusted"] is False
        assert proto["author"] == "adam"

    async def test_delete_removes_prototype(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        await handler.handle_create_prototype(
            StubRequest(body={"name": "hand", "behavior": self._BEHAVIOR})
        )

        resp = await handler.handle_delete_prototype(StubRequest(name="hand"))

        assert resp.status == 200
        assert json.loads(resp.text)["status"] == "success"
        assert get_prototype_info("hand", tlon_paths=p) is None

    async def test_delete_unknown_404(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)

        resp = await handler.handle_delete_prototype(StubRequest(name="nope"))

        assert resp.status == 404
        assert json.loads(resp.text)["reason"] == "prototype_not_found"

    async def test_list_includes_parameters(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        await handler.handle_create_prototype(
            StubRequest(body={"name": "tpl", "behavior": self._TEMPLATED})
        )

        resp = await handler.handle_list_prototypes(StubRequest())

        assert resp.status == 200
        protos = json.loads(resp.text)["prototypes"]
        assert protos[0]["parameters"] == [
            {"name": "to", "type": "string-list", "required": True}
        ]

    def test_staging_routes_precede_named_routes(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        app = handler.create_app()
        canonicals = [
            (route.method, route.resource.canonical) for route in app.router.routes()
        ]
        for method in ("GET", "PATCH"):
            staging_idx = canonicals.index((method, "/admin/prototypes/staging/{name}"))
            named_idx = canonicals.index((method, "/admin/prototypes/{name}"))
            assert staging_idx < named_idx


class TestAdminPrototypeLessons:
    """LESSONS.md on the prototype endpoints (issue ): POST/PATCH accept
    an optional ``lessons`` body field; the detail payload carries the text
    back so a reviewer sees the packaged lessons."""

    _BEHAVIOR = "{:version 1 :rules [{:name \"r\" :on :boot :actions []}]}\n"

    def _handler(self, tmp_path: Path) -> tuple[RESTAPIHandler, TlonPaths]:
        p = TlonPaths(home=tmp_path)
        return RESTAPIHandler(None, MagicMock(), paths=p), p

    async def test_create_with_lessons_and_detail_returns_it(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)

        resp = await handler.handle_create_prototype(
            StubRequest(body={"name": "hand", "behavior": self._BEHAVIOR,
                              "lessons": "- 前辈教训\n"})
        )

        assert resp.status == 201
        assert (p.prototypes_dir() / "hand" / "LESSONS.md").exists()

        resp = await handler.handle_get_prototype(StubRequest(name="hand"))
        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["lessons"] == "- 前辈教训\n"

    async def test_detail_without_lessons_omits_field(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        await handler.handle_create_prototype(
            StubRequest(body={"name": "hand", "behavior": self._BEHAVIOR})
        )

        resp = await handler.handle_get_prototype(StubRequest(name="hand"))

        assert resp.status == 200
        assert "lessons" not in json.loads(resp.text)["prototype"]

    async def test_patch_lessons_trusted(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        await handler.handle_create_prototype(
            StubRequest(body={"name": "hand", "behavior": self._BEHAVIOR})
        )

        resp = await handler.handle_update_prototype(
            StubRequest(name="hand", body={"lessons": "- 补充教训\n"})
        )

        assert resp.status == 200
        _info, _behavior, lessons, _reviews = get_prototype_detail("hand", tlon_paths=p)
        assert lessons == "- 补充教训\n"

    async def test_patch_lessons_staging(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        write_staging_prototype("staged", self._BEHAVIOR, author_id="adam", tlon_paths=p)

        resp = await handler.handle_update_staging_prototype(
            StubRequest(name="staged", body={"lessons": "- 审稿补充\n"})
        )

        assert resp.status == 200
        _info, _behavior, lessons, _reviews = get_prototype_detail("staged", staging=True, tlon_paths=p)
        assert lessons == "- 审稿补充\n"

    async def test_staging_detail_carries_lessons(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        write_staging_prototype(
            "staged", self._BEHAVIOR, author_id="adam", lessons="- 随包\n", tlon_paths=p
        )

        resp = await handler.handle_get_staging_prototype(StubRequest(name="staged"))

        assert resp.status == 200
        assert json.loads(resp.text)["prototype"]["lessons"] == "- 随包\n"


class TestAdminPrototypeReviews:
    """REVIEW.md peer-review records on the prototype detail endpoints
    (issue ): the payload carries the review text so the human
    approver sees the heterogeneous reviews; absent file = no field."""

    _BEHAVIOR = "{:version 1 :rules [{:name \"r\" :on :boot :actions []}]}\n"

    def _handler(self, tmp_path: Path) -> tuple[RESTAPIHandler, TlonPaths]:
        p = TlonPaths(home=tmp_path)
        return RESTAPIHandler(None, MagicMock(), paths=p), p

    async def test_staging_detail_carries_reviews(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        write_staging_prototype("staged", self._BEHAVIOR, author_id="adam", tlon_paths=p)
        append_staging_review(
            "staged", "## t — r-1（fake/model）：通过\n\n理由\n", tlon_paths=p
        )

        resp = await handler.handle_get_staging_prototype(StubRequest(name="staged"))

        assert resp.status == 200
        reviews = json.loads(resp.text)["prototype"]["reviews"]
        assert "r-1" in reviews and "理由" in reviews

    async def test_detail_without_reviews_omits_field(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        await handler.handle_create_prototype(
            StubRequest(body={"name": "hand", "behavior": self._BEHAVIOR})
        )

        resp = await handler.handle_get_prototype(StubRequest(name="hand"))

        assert resp.status == 200
        assert "reviews" not in json.loads(resp.text)["prototype"]


class TestAdminPrototypeLineage:
    """Lineage fields on the prototype endpoints (issue ): payloads carry
    parent/generation/proposal_note, detail endpoints embed the derived
    lineage chain with per-generation diffs."""

    _BEHAVIOR = "{:version 1 :rules [{:name \"r\" :on :boot :actions []}]}\n"
    _BEHAVIOR_V2 = (
        "{:version 1 :rules [{:name \"r2\" :on :boot :actions []}]}\n"
    )

    def _handler(self, tmp_path: Path) -> tuple[RESTAPIHandler, TlonPaths]:
        p = TlonPaths(home=tmp_path)
        return RESTAPIHandler(None, MagicMock(), paths=p), p

    async def test_create_with_parent_derives_generation(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        await handler.handle_create_prototype(
            StubRequest(body={"name": "base", "behavior": self._BEHAVIOR})
        )

        resp = await handler.handle_create_prototype(
            StubRequest(body={
                "name": "child",
                "behavior": self._BEHAVIOR_V2,
                "parent": "base",
                "proposal_note": "改规则名",
            })
        )

        assert resp.status == 201
        proto = json.loads(resp.text)["prototype"]
        assert proto["parent"] == "base"
        assert proto["generation"] == 2
        assert proto["proposal_note"] == "改规则名"

    async def test_create_unknown_parent_400(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)

        resp = await handler.handle_create_prototype(
            StubRequest(body={"name": "orphan", "behavior": self._BEHAVIOR,
                              "parent": "ghost"})
        )

        assert resp.status == 400
        assert json.loads(resp.text)["reason"] == "invalid_prototype"

    async def test_detail_includes_lineage_with_diff(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        await handler.handle_create_prototype(
            StubRequest(body={"name": "base", "behavior": self._BEHAVIOR})
        )
        await handler.handle_create_prototype(
            StubRequest(body={"name": "child", "behavior": self._BEHAVIOR_V2,
                              "parent": "base", "proposal_note": "改规则名"})
        )

        resp = await handler.handle_get_prototype(StubRequest(name="child"))

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["generation"] == 2
        lineage = proto["lineage"]
        assert [e["name"] for e in lineage] == ["base", "child"]
        assert lineage[0]["diff_from_parent"] is None
        assert "r2" in lineage[1]["diff_from_parent"]

    async def test_staging_detail_includes_lineage(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        write_staging_prototype("staged", self._BEHAVIOR, author_id="adam", tlon_paths=p)

        resp = await handler.handle_get_staging_prototype(StubRequest(name="staged"))

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["parent"] is None
        assert proto["generation"] == 1
        assert proto["proposal_note"] is None
        assert [e["name"] for e in proto["lineage"]] == ["staged"]

    async def test_update_accepts_parent(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        await handler.handle_create_prototype(
            StubRequest(body={"name": "base", "behavior": self._BEHAVIOR})
        )
        await handler.handle_create_prototype(
            StubRequest(body={"name": "child", "behavior": self._BEHAVIOR_V2})
        )

        resp = await handler.handle_update_prototype(
            StubRequest(name="child", body={"parent": "base", "proposal_note": "挂上谱系"})
        )

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["parent"] == "base"
        assert proto["generation"] == 2
        assert proto["proposal_note"] == "挂上谱系"

    async def test_list_payloads_carry_lineage_fields(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        await handler.handle_create_prototype(
            StubRequest(body={"name": "base", "behavior": self._BEHAVIOR})
        )

        resp = await handler.handle_list_prototypes(StubRequest())

        proto = json.loads(resp.text)["prototypes"][0]
        assert proto["parent"] is None
        assert proto["generation"] == 1
        assert proto["proposal_note"] is None


class TestGetAgentLineageFields:
    """GET /agents/{id} detail carries the same lineage fields as the list
    (previously missing spawned_by/spawn_depth — a documented inconsistency)."""

    async def test_detail_includes_spawned_by_and_depth(self, tmp_path: Path):
        from types import SimpleNamespace

        reg = MagicMock()
        reg.get_config.return_value = SimpleNamespace(
            name="新工程师", prototype="developer",
            spawned_by="gu-yanxi", spawn_depth=1,
        )
        reg.get_agent.return_value = None
        handler = RESTAPIHandler(None, reg, paths=TlonPaths(home=tmp_path))

        resp = await handler.handle_get_agent(StubRequest(agent_id="dev-1"))

        assert resp.status == 200
        body = json.loads(resp.text)
        assert body["config"]["prototype"] == "developer"
        assert body["config"]["spawned_by"] == "gu-yanxi"
        assert body["config"]["spawn_depth"] == 1

