"""Admin REST API — agent CRUD + prototype approval (wizard removal, A2).

No HTTP server is started: handlers are called directly with stub requests
and a stub registry (wrapped in the real tlon.api facades), against a real
temp TlonPaths so instantiate / persona writes / staging approval exercise
the real paths.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.identity_helpers import make_identity
from tests.prototype_helpers import builtin_id
from tlon.agents.prototypes.catalog import (
    get_prototype_detail,
    get_prototype_info,
    seed_prototypes,
)
from tlon.agents.prototypes.staging import (
    append_staging_review,
    write_staging_prototype,
)
from tlon.api.config import ConfigFacade
from tlon.api.members import MembersFacade
from tlon.api.prototypes import PrototypesFacade
from tlon.base.config import yaml_load_all
from tlon.base.paths import TlonPaths
from tlon.tools.agents.ports import StopRejectedError
from tlon_server.api.rest import RESTAPIHandler


class StubRequest:
    """Minimal stand-in for aiohttp.web.Request."""

    def __init__(
        self, body: dict | None = None, agent_id: str | None = None, name: str | None = None
    ):
        self._body = body or {}
        self.match_info = {}
        if agent_id is not None:
            self.match_info["agent_id"] = agent_id
        if name is not None:
            self.match_info["name"] = name

    async def json(self) -> dict:
        return self._body


def _handler(
    tmp_path: Path, registry: MagicMock | None = None
) -> tuple[RESTAPIHandler, MagicMock, TlonPaths]:
    p = TlonPaths(home=tmp_path)
    seed_prototypes(p)
    reg = registry
    if reg is None:
        reg = MagicMock()
        reg.adopt_agent = AsyncMock(return_value="started")
        reg.remove_member = AsyncMock(return_value=True)
    handler = RESTAPIHandler(
        None,
        identity=make_identity(),
        members=MembersFacade(agent_registry=reg, tlon_paths=p),
        configs=ConfigFacade(config_service=MagicMock(), tlon_paths=p),
        prototypes=PrototypesFacade(tlon_paths=p),
    )
    return handler, reg, p


class TestAdminCreateAgent:
    async def test_create_from_prototype_with_persona(self, tmp_path: Path):
        handler, reg, p = _handler(tmp_path)
        req = StubRequest(
            body={
                "prototype": builtin_id("计划后执行"),
                "name": "新工程师",
                "model": "deepseek/deepseek-chat",
                "persona": {"identity": "你是新工程师", "profile": "开发", "behavior": "干活"},
            }
        )

        resp = await handler.handle_create_agent(req)

        assert resp.status == 200
        body = json.loads(resp.text)
        agent_id = body["agent_id"]
        assert len(agent_id) == 34 and agent_id.startswith("m-")
        assert body["prototype"] == builtin_id("计划后执行")
        reg.adopt_agent.assert_awaited_once_with(agent_id)

        configs = yaml_load_all(p.tlon_home())
        assert configs[agent_id].prototype == builtin_id("计划后执行")
        # member-level LLM config (ADR 0016): the model lands in agent.yaml
        assert configs[agent_id].llm is not None
        assert configs[agent_id].llm.model_name == "deepseek/deepseek-chat"
        behavior = p.agent_behavior_file(agent_id)
        assert behavior.exists() and "${" not in behavior.read_text(encoding="utf-8")
        identity = p.agent_config_dir(agent_id) / "IDENTITY.md"
        assert identity.read_text(encoding="utf-8").strip() == "你是新工程师"

    @pytest.mark.parametrize("legacy_id", ["alice", "developer", "", None])
    async def test_caller_member_id_is_rejected(
        self, tmp_path: Path, legacy_id: str | None
    ) -> None:
        handler, registry, paths = _handler(tmp_path)
        response = await handler.handle_create_agent(
            StubRequest(
                body={
                    "prototype": builtin_id("计划后执行"),
                    "name": "开发工程师",
                    "model": "deepseek/deepseek-chat",
                    "agent_id": legacy_id,
                }
            )
        )
        assert response.status == 400
        assert json.loads(response.text)["reason"] == "managed_id"
        registry.adopt_agent.assert_not_awaited()
        assert yaml_load_all(paths.tlon_home()) == {}

    async def test_generation_avoids_webui_registered_human_and_disk_ids(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from tlon.organization.mail.store import MailStore

        handler, registry, paths = _handler(tmp_path)
        registry.get_all_ids.return_value = ["m-registered"]
        paths.agent_config_dir("m-offline").mkdir(parents=True)
        monkeypatch.setattr(MailStore, "known_human_ids", lambda self: ["human:m-human"])
        candidates = iter(["alice", "m-human", "m-offline", "m-registered", "m-" + "a" * 32])
        monkeypatch.setattr("tlon.api.members.new_member_id", lambda: next(candidates))
        response = await handler.handle_create_agent(
            StubRequest(
                body={
                    "prototype": builtin_id("计划后执行"),
                    "name": "开发工程师",
                    "model": "deepseek/deepseek-chat",
                }
            )
        )
        assert response.status == 200
        agent_id = json.loads(response.text)["agent_id"]
        assert agent_id == "m-" + "a" * 32
        registry.adopt_agent.assert_awaited_once_with(agent_id)
        assert set(yaml_load_all(paths.tlon_home())) == {agent_id}
        assert not paths.agent_config_dir("alice").exists()
        assert not paths.agent_config_dir("m-human").exists()

    async def test_missing_fields_rejected(self, tmp_path: Path):
        handler, _reg, _p = _handler(tmp_path)

        resp = await handler.handle_create_agent(
            StubRequest(body={"prototype": builtin_id("计划后执行")})
        )

        assert resp.status == 400

    async def test_missing_model_rejected(self, tmp_path: Path):
        """ADR 0016: model is required — no process-level fallback."""
        handler, _reg, _p = _handler(tmp_path)

        resp = await handler.handle_create_agent(
            StubRequest(body={"prototype": builtin_id("计划后执行"), "name": "x"})
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
            route.method == "POST" and route.resource.canonical == "/admin/config/agents"
            for route in app.router.routes()
        )


class TestAdminUpdateAgent:
    @pytest.mark.parametrize("agent_id", ["alice", "ALICE"])
    @pytest.mark.parametrize(
        "body",
        [
            {"persona": {"identity": "must not be written"}},
            {
                "persona": {"identity": "must not be written"},
                "llm": {"model_name": "other-model"},
            },
        ],
    )
    async def test_offline_identity_collision_rejected_before_writes_or_adopt(
        self, tmp_path: Path, agent_id: str, body: dict[str, object]
    ) -> None:
        from tlon.agents.prototypes.instantiate import instantiate

        handler, registry, paths = _handler(tmp_path)
        # Supported offline creation does not pass the REST create/reload guards.
        instantiate(
            builtin_id("计划后执行"),
            agent_id,
            "离线成员",
            model="openai/gpt-5",
            tlon_paths=paths,
        )
        config_dir = paths.agent_config_dir(agent_id)
        before = {file.name: file.read_bytes() for file in config_dir.iterdir()}

        response = await handler.handle_update_agent(StubRequest(body=body, agent_id=agent_id))

        assert response.status == 409
        assert json.loads(response.text)["reason"] == "identity_conflict"
        registry.adopt_agent.assert_not_awaited()
        assert {file.name: file.read_bytes() for file in config_dir.iterdir()} == before

    async def test_update_persona_and_llm(self, tmp_path: Path):
        handler, reg, p = _handler(tmp_path)
        from tlon.agents.prototypes.instantiate import instantiate

        instantiate(
            builtin_id("计划后执行"), "dev-1", "新工程师", model="openai/gpt-5", tlon_paths=p
        )
        req = StubRequest(
            body={
                "persona": {"identity": "新身份"},
                "llm": {"model_name": "other-model"},
            },
            agent_id="dev-1",
        )

        resp = await handler.handle_update_agent(req)

        assert resp.status == 200
        identity = p.agent_config_dir("dev-1") / "IDENTITY.md"
        assert identity.read_text(encoding="utf-8").strip() == "新身份"
        cfg = yaml_load_all(p.tlon_home())["dev-1"]
        assert cfg.llm.model_name == "other-model"
        reg.adopt_agent.assert_awaited_once_with("dev-1")

    async def test_unknown_agent_404(self, tmp_path: Path):
        handler, _reg, _p = _handler(tmp_path)

        resp = await handler.handle_update_agent(StubRequest(body={}, agent_id="nobody"))

        assert resp.status == 404


class TestAdminDeleteAgent:
    async def test_delete_calls_remove_member(self, tmp_path: Path):
        handler, reg, _p = _handler(tmp_path)

        resp = await handler.handle_delete_agent(StubRequest(agent_id="dev-1"))

        assert resp.status == 200
        reg.remove_member.assert_awaited_once_with("dev-1", removed_by="admin-api")

    async def test_live_sessions_conflict(self, tmp_path: Path):
        reg = MagicMock()
        reg.remove_member = AsyncMock(side_effect=StopRejectedError("active_sessions", "busy"))
        handler, _reg, _p = _handler(tmp_path, reg)

        resp = await handler.handle_delete_agent(StubRequest(agent_id="dev-1"))

        assert resp.status == 409
        assert json.loads(resp.text)["reason"] == "active_sessions"


class TestAdminStaging:
    async def _staged(self, tmp_path: Path) -> tuple[TlonPaths, str]:
        p = TlonPaths(home=tmp_path)
        seed_prototypes(p)
        info = write_staging_prototype(
            '{:version 1 :rules [{:name "r" :on :boot :actions []}]}\n',
            display_name="LLM 创意",
            description="由 agent 创作",
            author_id="gu-yanxi",
            tlon_paths=p,
        )
        return p, info.name

    async def test_list_approve_reject(self, tmp_path: Path):
        p, staged_id = await self._staged(tmp_path)
        handler = RESTAPIHandler(
            None, identity=make_identity(), prototypes=PrototypesFacade(tlon_paths=p)
        )

        listed = await handler.handle_list_staging(StubRequest())
        assert listed.status == 200
        names = [x["name"] for x in json.loads(listed.text)["prototypes"]]
        assert names == [staged_id]

        approved = await handler.handle_approve_prototype(StubRequest(name=staged_id))
        assert approved.status == 200
        info = get_prototype_info(staged_id, tlon_paths=p)
        assert info is not None and info.trusted

        # Staging list is now empty; approving again fails.
        listed2 = await handler.handle_list_staging(StubRequest())
        assert json.loads(listed2.text)["prototypes"] == []

    async def test_reject_removes_staging(self, tmp_path: Path):
        p, staged_id = await self._staged(tmp_path)
        handler = RESTAPIHandler(
            None, identity=make_identity(), prototypes=PrototypesFacade(tlon_paths=p)
        )

        rejected = await handler.handle_reject_prototype(StubRequest(name=staged_id))
        assert rejected.status == 200
        assert get_prototype_info(staged_id, tlon_paths=p) is None
        assert not (p.prototypes_staging_dir() / staged_id).exists()


class TestAdminPrototypeManagement:
    """Prototype management endpoints (WebUI PROTOTYPES view): detail,
    human-authored create, edit, delete."""

    _BEHAVIOR = '{:version 1 :rules [{:name "r" :on :boot :actions []}]}\n'
    _TEMPLATED = (
        "{:version 1\n"
        " :parameters {:to {:type :string-list :required true}}\n"
        ' :rules [{:name "relay" :on :mail-delivered'
        " :actions [(forward {:to ${to}})]}]}\n"
    )
    _INVALID = (
        '{:version 1 :rules [{:name "w" :on :mail-delivered'
        ' :actions [(jump {:to ["x@tlon.internal"]})]}]}\n'
    )

    def _handler(self, tmp_path: Path) -> tuple[RESTAPIHandler, TlonPaths]:
        p = TlonPaths(home=tmp_path)
        return RESTAPIHandler(
            None, identity=make_identity(), prototypes=PrototypesFacade(tlon_paths=p)
        ), p

    async def test_create_writes_trusted_library(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)

        resp = await handler.handle_create_prototype(
            StubRequest(body={"behavior": self._BEHAVIOR, "display_name": "手写"})
        )
        hand_id = json.loads(resp.text)["prototype"]["name"]

        assert resp.status == 201
        proto = json.loads(resp.text)["prototype"]
        assert len(proto["name"]) == 34 and proto["name"].startswith("p-")
        assert proto["trusted"] is True
        assert proto["author"] is None
        assert proto["parameters"] == []
        info = get_prototype_info(hand_id, tlon_paths=p)
        assert info is not None and info.trusted

    @pytest.mark.parametrize("legacy_id", ["developer", "", None])
    async def test_create_rejects_caller_id(self, tmp_path: Path, legacy_id: str | None) -> None:
        handler, paths = self._handler(tmp_path)
        response = await handler.handle_create_prototype(
            StubRequest(
                body={
                    "name": legacy_id,
                    "display_name": "标准迭代",
                    "behavior": self._BEHAVIOR,
                }
            )
        )
        assert response.status == 400
        assert json.loads(response.text)["reason"] == "managed_id"
        assert not paths.prototypes_dir().exists()

    @pytest.mark.parametrize("display_name", [None, "", "   "])
    async def test_create_requires_behavior_label(
        self, tmp_path: Path, display_name: str | None
    ) -> None:
        handler, paths = self._handler(tmp_path)
        response = await handler.handle_create_prototype(
            StubRequest(
                body={
                    "display_name": display_name,
                    "behavior": self._BEHAVIOR,
                }
            )
        )
        assert response.status == 400
        assert json.loads(response.text)["reason"] == "invalid_prototype"
        assert not paths.prototypes_dir().exists()

    async def test_create_missing_fields_400(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)

        resp = await handler.handle_create_prototype(StubRequest(body={}))

        assert resp.status == 400
        assert json.loads(resp.text)["reason"] == "invalid_prototype"

    async def test_create_invalid_behavior_400_nothing_on_disk(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)

        resp = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "bad", "behavior": self._INVALID})
        )

        assert resp.status == 400
        assert json.loads(resp.text)["reason"] == "invalid_prototype"
        assert not (p.prototypes_dir() / "bad").exists()

    async def test_detail_includes_behavior_and_parameters(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        tpl_id_response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "tpl", "behavior": self._TEMPLATED})
        )
        tpl_id = json.loads(tpl_id_response.text)["prototype"]["name"]

        resp = await handler.handle_get_prototype(StubRequest(name=tpl_id))

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["behavior"] == self._TEMPLATED
        assert proto["parameters"] == [{"name": "to", "type": "string-list", "required": True}]

    async def test_detail_unknown_404(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)

        resp = await handler.handle_get_prototype(StubRequest(name="nope"))

        assert resp.status == 404
        assert json.loads(resp.text)["reason"] == "prototype_not_found"

    async def test_staging_detail_is_untrusted(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        staged_id = write_staging_prototype(
            self._BEHAVIOR, author_id="adam", tlon_paths=p, display_name="staged"
        ).name

        resp = await handler.handle_get_staging_prototype(StubRequest(name=staged_id))

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["trusted"] is False
        assert proto["author"] == "adam"

    _L2_BEHAVIOR = (
        '{:version 1 :execution {:level :L2} :rules [{:name "r" :on :boot :actions []}]}\n'
    )
    _L1_BEHAVIOR = (
        '{:version 1 :execution {:level :L1} :rules [{:name "r" :on :boot :actions []}]}\n'
    )

    async def test_staging_l2_detail_raises_red_flag(self, tmp_path: Path):
        """Constitution 1: a staging proposal declaring L2 (full Linux
        userland) is a review red flag by itself — derived into the detail
        payload for the human approver."""
        handler, p = self._handler(tmp_path)
        staged_id = write_staging_prototype(
            self._L2_BEHAVIOR, author_id="adam", tlon_paths=p, display_name="staged"
        ).name

        resp = await handler.handle_get_staging_prototype(StubRequest(name=staged_id))

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["execution_level"] == "L2"
        assert proto["red_flags"] == ["execution-level-l2"]

    async def test_trusted_l2_detail_has_no_red_flag(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        hand_id_response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "hand", "behavior": self._L2_BEHAVIOR})
        )
        hand_id = json.loads(hand_id_response.text)["prototype"]["name"]

        resp = await handler.handle_get_prototype(StubRequest(name=hand_id))

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["execution_level"] == "L2"
        assert proto["red_flags"] == []

    async def test_staging_l1_detail_has_no_red_flag(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        staged_id = write_staging_prototype(
            self._L1_BEHAVIOR, author_id="adam", tlon_paths=p, display_name="staged"
        ).name

        resp = await handler.handle_get_staging_prototype(StubRequest(name=staged_id))

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["execution_level"] == "L1"
        assert proto["red_flags"] == []

    async def test_unparseable_behavior_detail_does_not_500(self, tmp_path: Path):
        """A broken spec on disk (e.g. hand-edited behind the write gate)
        degrades the derived fields, never the endpoint."""
        handler, p = self._handler(tmp_path)
        staged_id = write_staging_prototype(
            self._BEHAVIOR, author_id="adam", tlon_paths=p, display_name="staged"
        ).name
        (p.prototypes_staging_dir() / staged_id / "behavior.tlon").write_text(
            "{:version 1 :rules [", encoding="utf-8"
        )

        resp = await handler.handle_get_staging_prototype(StubRequest(name=staged_id))

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["execution_level"] is None
        assert proto["red_flags"] == []

    async def test_update_metadata_and_behavior(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        hand_id_response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "hand", "behavior": self._BEHAVIOR})
        )
        hand_id = json.loads(hand_id_response.text)["prototype"]["name"]

        resp = await handler.handle_update_prototype(
            StubRequest(name=hand_id, body={"description": "新描述", "behavior": self._TEMPLATED})
        )

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["description"] == "新描述"
        assert proto["trusted"] is True
        _info, behavior, _, _reviews = get_prototype_detail(hand_id, tlon_paths=p)
        assert behavior == self._TEMPLATED

    async def test_update_empty_body_400(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        hand_id_response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "hand", "behavior": self._BEHAVIOR})
        )
        hand_id = json.loads(hand_id_response.text)["prototype"]["name"]

        resp = await handler.handle_update_prototype(StubRequest(name=hand_id, body={}))

        assert resp.status == 400
        assert json.loads(resp.text)["reason"] == "invalid_prototype"

    async def test_update_invalid_behavior_leaves_disk_untouched(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        hand_id_response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "hand", "behavior": self._BEHAVIOR})
        )
        hand_id = json.loads(hand_id_response.text)["prototype"]["name"]

        resp = await handler.handle_update_prototype(
            StubRequest(name=hand_id, body={"behavior": self._INVALID})
        )

        assert resp.status == 400
        assert json.loads(resp.text)["reason"] == "invalid_prototype"
        _info, behavior, _, _reviews = get_prototype_detail(hand_id, tlon_paths=p)
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
        staged_id = write_staging_prototype(
            self._BEHAVIOR, author_id="adam", tlon_paths=p, display_name="staged"
        ).name

        resp = await handler.handle_update_staging_prototype(
            StubRequest(name=staged_id, body={"display_name": "改名"})
        )

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["trusted"] is False
        assert proto["author"] == "adam"

    async def test_delete_removes_prototype(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        hand_id_response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "hand", "behavior": self._BEHAVIOR})
        )
        hand_id = json.loads(hand_id_response.text)["prototype"]["name"]

        resp = await handler.handle_delete_prototype(StubRequest(name=hand_id))

        assert resp.status == 200
        assert json.loads(resp.text)["status"] == "success"
        assert get_prototype_info(hand_id, tlon_paths=p) is None

    async def test_delete_unknown_404(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)

        resp = await handler.handle_delete_prototype(StubRequest(name="nope"))

        assert resp.status == 404
        assert json.loads(resp.text)["reason"] == "prototype_not_found"

    async def test_list_includes_parameters(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        tpl_id_response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "tpl", "behavior": self._TEMPLATED})
        )
        assert tpl_id_response.status == 201

        resp = await handler.handle_list_prototypes(StubRequest())

        assert resp.status == 200
        protos = json.loads(resp.text)["prototypes"]
        assert protos[0]["parameters"] == [{"name": "to", "type": "string-list", "required": True}]

    def test_staging_routes_precede_named_routes(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        app = handler.create_app()
        canonicals = [(route.method, route.resource.canonical) for route in app.router.routes()]
        for method in ("GET", "PATCH"):
            staging_idx = canonicals.index((method, "/admin/prototypes/staging/{name}"))
            named_idx = canonicals.index((method, "/admin/prototypes/{name}"))
            assert staging_idx < named_idx


class TestAdminPrototypeLessons:
    """LESSONS.md on the prototype endpoints (docs/design/prototypes.md):
    POST/PATCH accept
    an optional ``lessons`` body field; the detail payload carries the text
    back so a reviewer sees the packaged lessons."""

    _BEHAVIOR = '{:version 1 :rules [{:name "r" :on :boot :actions []}]}\n'

    def _handler(self, tmp_path: Path) -> tuple[RESTAPIHandler, TlonPaths]:
        p = TlonPaths(home=tmp_path)
        return RESTAPIHandler(
            None, identity=make_identity(), prototypes=PrototypesFacade(tlon_paths=p)
        ), p

    async def test_create_with_lessons_and_detail_returns_it(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)

        resp = await handler.handle_create_prototype(
            StubRequest(
                body={"display_name": "hand", "behavior": self._BEHAVIOR, "lessons": "- 前辈教训\n"}
            )
        )
        hand_id = json.loads(resp.text)["prototype"]["name"]

        assert resp.status == 201
        assert (p.prototypes_dir() / hand_id / "LESSONS.md").exists()

        resp = await handler.handle_get_prototype(StubRequest(name=hand_id))
        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["lessons"] == "- 前辈教训\n"

    async def test_detail_without_lessons_omits_field(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        hand_id_response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "hand", "behavior": self._BEHAVIOR})
        )
        hand_id = json.loads(hand_id_response.text)["prototype"]["name"]

        resp = await handler.handle_get_prototype(StubRequest(name=hand_id))

        assert resp.status == 200
        assert "lessons" not in json.loads(resp.text)["prototype"]

    async def test_patch_lessons_trusted(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        hand_id_response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "hand", "behavior": self._BEHAVIOR})
        )
        hand_id = json.loads(hand_id_response.text)["prototype"]["name"]

        resp = await handler.handle_update_prototype(
            StubRequest(name=hand_id, body={"lessons": "- 补充教训\n"})
        )

        assert resp.status == 200
        _info, _behavior, lessons, _reviews = get_prototype_detail(hand_id, tlon_paths=p)
        assert lessons == "- 补充教训\n"

    async def test_patch_lessons_staging(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        staged_id = write_staging_prototype(
            self._BEHAVIOR, author_id="adam", tlon_paths=p, display_name="staged"
        ).name

        resp = await handler.handle_update_staging_prototype(
            StubRequest(name=staged_id, body={"lessons": "- 审稿补充\n"})
        )

        assert resp.status == 200
        _info, _behavior, lessons, _reviews = get_prototype_detail(
            staged_id, staging=True, tlon_paths=p
        )
        assert lessons == "- 审稿补充\n"

    async def test_staging_detail_carries_lessons(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        staged_id = write_staging_prototype(
            self._BEHAVIOR,
            author_id="adam",
            lessons="- 随包\n",
            tlon_paths=p,
            display_name="staged",
        ).name

        resp = await handler.handle_get_staging_prototype(StubRequest(name=staged_id))

        assert resp.status == 200
        assert json.loads(resp.text)["prototype"]["lessons"] == "- 随包\n"


class TestAdminPrototypeReviews:
    """REVIEW.md peer-review records on the prototype detail endpoints
    (docs/design/prototypes.md): the payload carries the review text so
    the human
    approver sees the heterogeneous reviews; absent file = no field."""

    _BEHAVIOR = '{:version 1 :rules [{:name "r" :on :boot :actions []}]}\n'

    def _handler(self, tmp_path: Path) -> tuple[RESTAPIHandler, TlonPaths]:
        p = TlonPaths(home=tmp_path)
        return RESTAPIHandler(
            None, identity=make_identity(), prototypes=PrototypesFacade(tlon_paths=p)
        ), p

    async def test_staging_detail_carries_reviews(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        staged_id = write_staging_prototype(
            self._BEHAVIOR, author_id="adam", tlon_paths=p, display_name="staged"
        ).name
        append_staging_review(staged_id, "## t — r-1（fake/model）：通过\n\n理由\n", tlon_paths=p)

        resp = await handler.handle_get_staging_prototype(StubRequest(name=staged_id))

        assert resp.status == 200
        reviews = json.loads(resp.text)["prototype"]["reviews"]
        assert "r-1" in reviews and "理由" in reviews

    async def test_detail_without_reviews_omits_field(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        hand_id_response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "hand", "behavior": self._BEHAVIOR})
        )
        hand_id = json.loads(hand_id_response.text)["prototype"]["name"]

        resp = await handler.handle_get_prototype(StubRequest(name=hand_id))

        assert resp.status == 200
        assert "reviews" not in json.loads(resp.text)["prototype"]


class TestAdminPrototypeLineage:
    """Lineage fields on the prototype endpoints (docs/design/prototypes.md):
    payloads carry
    parent/generation/proposal_note, detail endpoints embed the derived
    lineage chain with per-generation diffs."""

    _BEHAVIOR = '{:version 1 :rules [{:name "r" :on :boot :actions []}]}\n'
    _BEHAVIOR_V2 = '{:version 1 :rules [{:name "r2" :on :boot :actions []}]}\n'

    def _handler(self, tmp_path: Path) -> tuple[RESTAPIHandler, TlonPaths]:
        p = TlonPaths(home=tmp_path)
        return RESTAPIHandler(
            None, identity=make_identity(), prototypes=PrototypesFacade(tlon_paths=p)
        ), p

    async def test_create_with_parent_derives_generation(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        base_id_response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "base", "behavior": self._BEHAVIOR})
        )
        base_id = json.loads(base_id_response.text)["prototype"]["name"]

        resp = await handler.handle_create_prototype(
            StubRequest(
                body={
                    "display_name": "child",
                    "behavior": self._BEHAVIOR_V2,
                    "parent": base_id,
                    "proposal_note": "改规则名",
                }
            )
        )
        assert resp.status == 201

        assert resp.status == 201
        proto = json.loads(resp.text)["prototype"]
        assert proto["parent"] == base_id
        assert proto["generation"] == 2
        assert proto["proposal_note"] == "改规则名"

    async def test_create_unknown_parent_400(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)

        resp = await handler.handle_create_prototype(
            StubRequest(
                body={"display_name": "orphan", "behavior": self._BEHAVIOR, "parent": "ghost"}
            )
        )

        assert resp.status == 400
        assert json.loads(resp.text)["reason"] == "invalid_prototype"

    async def test_detail_includes_lineage_with_diff(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        base_id_response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "base", "behavior": self._BEHAVIOR})
        )
        base_id = json.loads(base_id_response.text)["prototype"]["name"]
        child_id_response = await handler.handle_create_prototype(
            StubRequest(
                body={
                    "display_name": "child",
                    "behavior": self._BEHAVIOR_V2,
                    "parent": base_id,
                    "proposal_note": "改规则名",
                }
            )
        )
        child_id = json.loads(child_id_response.text)["prototype"]["name"]

        resp = await handler.handle_get_prototype(StubRequest(name=child_id))

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["generation"] == 2
        lineage = proto["lineage"]
        assert [e["name"] for e in lineage] == [base_id, child_id]
        assert lineage[0]["diff_from_parent"] is None
        assert "r2" in lineage[1]["diff_from_parent"]

    async def test_staging_detail_includes_lineage(self, tmp_path: Path):
        handler, p = self._handler(tmp_path)
        staged_id = write_staging_prototype(
            self._BEHAVIOR, author_id="adam", tlon_paths=p, display_name="staged"
        ).name

        resp = await handler.handle_get_staging_prototype(StubRequest(name=staged_id))

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["parent"] is None
        assert proto["generation"] == 1
        assert proto["proposal_note"] is None
        assert [e["name"] for e in proto["lineage"]] == [staged_id]

    async def test_update_accepts_parent(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        base_id_response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "base", "behavior": self._BEHAVIOR})
        )
        base_id = json.loads(base_id_response.text)["prototype"]["name"]
        child_id_response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "child", "behavior": self._BEHAVIOR_V2})
        )
        child_id = json.loads(child_id_response.text)["prototype"]["name"]

        resp = await handler.handle_update_prototype(
            StubRequest(name=child_id, body={"parent": base_id, "proposal_note": "挂上谱系"})
        )

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["parent"] == base_id
        assert proto["generation"] == 2
        assert proto["proposal_note"] == "挂上谱系"

    async def test_list_payloads_carry_lineage_fields(self, tmp_path: Path):
        handler, _p = self._handler(tmp_path)
        base_id_response = await handler.handle_create_prototype(
            StubRequest(body={"display_name": "base", "behavior": self._BEHAVIOR})
        )
        assert base_id_response.status == 201

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
            name="新工程师",
            prototype=builtin_id("计划后执行"),
            spawned_by="gu-yanxi",
            spawn_depth=1,
        )
        reg.get_agent_status = AsyncMock(
            return_value={
                "id": "dev-1",
                "name": "新工程师",
                "state": "waiting",
                "wake_at": None,
            }
        )
        handler = RESTAPIHandler(
            None,
            identity=make_identity(),
            members=MembersFacade(agent_registry=reg, tlon_paths=TlonPaths(home=tmp_path)),
        )

        resp = await handler.handle_get_agent(StubRequest(agent_id="dev-1"))

        assert resp.status == 200
        body = json.loads(resp.text)
        assert body["config"]["prototype"] == builtin_id("计划后执行")
        assert body["config"]["spawned_by"] == "gu-yanxi"
        assert body["config"]["spawn_depth"] == 1


class TestSchedulingStateAPI:
    @pytest.mark.parametrize("state", ["active", "waiting", "sleeping", "dead"])
    async def test_detail_matches_authoritative_status(self, tmp_path: Path, state: str):
        handler, reg, _ = _handler(tmp_path)
        reg.get_config.return_value = MagicMock(prototype=None, spawned_by=None, spawn_depth=0)
        wake_at = "2030-01-01T00:00:00+00:00" if state == "sleeping" else None
        snapshot = {
            "id": "member",
            "name": "成员",
            "state": state,
            "wake_at": wake_at,
            "resident": False,
            "waking": False,
            "stop_pending": False,
            "error": None,
        }
        reg.get_agent_status = AsyncMock(return_value=snapshot)
        response = await handler.handle_get_agent(StubRequest(agent_id="member"))
        assert response.status == 200
        result = json.loads(response.text)
        assert result["state"] == state
        assert result["wake_at"] == wake_at
        assert result["resident"] is False
        reg.get_agent_status.assert_awaited_once_with("member")

    @pytest.mark.parametrize("finished,code", [(False, 202), (True, 200)])
    async def test_management_termination_drains_and_preserves_history(
        self, tmp_path: Path, finished: bool, code: int
    ):
        handler, reg, _ = _handler(tmp_path)
        reg.stop_member = AsyncMock(return_value=finished)
        reg.get_agent_status = AsyncMock(
            return_value={
                "id": "member",
                "state": "dead" if finished else "active",
                "stop_pending": not finished,
                "wake_at": None,
            }
        )
        response = await handler.handle_terminate_agent(StubRequest(agent_id="member"))
        assert response.status == code
        body = json.loads(response.text)
        assert body["stop_pending"] is not finished
        assert body["agent"]["state"] == ("dead" if finished else "active")
        reg.stop_member.assert_awaited_once_with("member", stopped_by="admin-api", when_idle=True)
        reg.remove_member.assert_not_called()

    async def test_terminate_unknown_member(self, tmp_path: Path):
        handler, reg, _ = _handler(tmp_path)
        reg.get_config.return_value = None
        response = await handler.handle_terminate_agent(StubRequest(agent_id="unknown"))
        assert response.status == 404
        reg.stop_member.assert_not_called()
