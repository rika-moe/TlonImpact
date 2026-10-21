"""Admin capability REST endpoints (docs/design/capabilities.md, issue #45
切片 3/3).

Same in-process pattern as test_rest_admin_arena.py: handlers are called
directly with stub requests. The capabilities facade is real (thin
pass-through) over a real temp TlonPaths, so list/detail/create/approve/
reject exercise the real staging layout.
"""

import json
from contextlib import suppress
from pathlib import Path
from typing import Any

from tests.identity_helpers import make_identity
from tlon.agents.capabilities.spec import CapabilityError
from tlon.agents.capabilities.staging import (
    append_staging_review,
    approve_capability,
    write_staging_capability,
)
from tlon.api.capabilities import CapabilitiesFacade
from tlon.base.paths import TlonPaths
from tlon_server.api.rest import RESTAPIHandler

VALID_CAPABILITY = """\
{:version 2 :name "thread-digest" :description "把邮件线程蒸馏成三段式摘要文本"
 :input-schema {:type "object" :properties {:mails {:type "array" :items true}} :required ["mails"] :additionalProperties false}
 :output-schema {:type "string"} :impl (fn [input] (str "digest:" (count (get input :mails))))}
"""

INVALID_CAPABILITY = """\
{:version 2 :name "wild" :description "组合未注册词汇"
 :input-schema {:type "object" :properties {} :required [] :additionalProperties false}
 :output-schema true :impl (fn [input] (jump "x"))}
"""

CAPABILITY_WITH_EXAMPLES = """\
{:version 2 :name "shout" :description "加感叹号"
 :input-schema {:type "object" :properties {:s {:type "string"}} :required ["s"] :additionalProperties false}
 :output-schema {:type "string"} :impl (fn [input] (str (get input :s) "!"))
 :examples [{:input {:s "hey"} :expect "hey!"}]}
"""

CAPABILITY_WITH_FAILING_EXAMPLE = """\
{:version 2 :name "shout" :description "失败例子是评审材料"
 :input-schema {:type "object" :properties {:s {:type "number"}} :required ["s"] :additionalProperties false}
 :output-schema {:type "number"} :impl (fn [input] (inc (get input :s)))
 :examples [{:input {:s 1} :expect 3} {:input {:s "a"} :expect 1}]}
"""


class StubRequest:
    """Minimal stand-in for aiohttp.web.Request."""

    def __init__(self, body: dict[str, Any] | None = None, name: str | None = None):
        self._body = body
        self.match_info: dict[str, str] = {}
        if name is not None:
            self.match_info["name"] = name

    async def json(self) -> dict[str, Any]:
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _handler(tmp_path: Path, wire: bool = True) -> tuple[RESTAPIHandler, TlonPaths]:
    p = TlonPaths(home=tmp_path)
    handler = RESTAPIHandler(
        None,
        identity=make_identity(),
        capabilities=CapabilitiesFacade(tlon_paths=p) if wire else None,
    )
    return handler, p


def _stage(p: TlonPaths, text: str = VALID_CAPABILITY) -> None:
    write_staging_capability(text, author_id="adam", proposal_note="新增摘要词", tlon_paths=p)


def _write_trusted(p: TlonPaths, text: str = VALID_CAPABILITY, name: str = "thread-digest") -> None:
    """Handwrite a capability directly into the trusted library."""
    cap_dir = p.capabilities_dir() / name
    cap_dir.mkdir(parents=True)
    (cap_dir / "capability.tlon").write_text(text, encoding="utf-8")
    # Intentionally invalid review artifacts remain visible, unapproved.
    with suppress(CapabilityError):
        approve_capability(name, p)


class TestListCapabilities:
    async def test_list_trusted(self, tmp_path: Path):
        handler, p = _handler(tmp_path)
        _write_trusted(p)

        resp = await handler.handle_list_capabilities(StubRequest())

        assert resp.status == 200
        body = json.loads(resp.text)
        assert body["status"] == "success"
        assert len(body["capabilities"]) == 1
        cap = body["capabilities"][0]
        assert cap["name"] == "thread-digest"
        assert cap["description"] == "把邮件线程蒸馏成三段式摘要文本"
        assert cap["effects"] == []
        assert cap["trusted"] is True
        assert cap["deprecated"] is False

    async def test_list_trusted_empty(self, tmp_path: Path):
        handler, _p = _handler(tmp_path)

        resp = await handler.handle_list_capabilities(StubRequest())

        assert resp.status == 200
        assert json.loads(resp.text)["capabilities"] == []

    async def test_list_staging(self, tmp_path: Path):
        handler, p = _handler(tmp_path)
        _stage(p)

        resp = await handler.handle_list_staging_capabilities(StubRequest())

        assert resp.status == 200
        caps = json.loads(resp.text)["capabilities"]
        assert len(caps) == 1
        assert caps[0]["name"] == "thread-digest"
        assert caps[0]["trusted"] is False
        assert caps[0]["author"] == "adam"
        assert caps[0]["proposal_note"] == "新增摘要词"

    async def test_lists_unwired_503(self, tmp_path: Path):
        handler, _p = _handler(tmp_path, wire=False)

        assert (await handler.handle_list_capabilities(StubRequest())).status == 503
        assert (await handler.handle_list_staging_capabilities(StubRequest())).status == 503


class TestCapabilityDetail:
    async def test_trusted_detail(self, tmp_path: Path):
        handler, p = _handler(tmp_path)
        _write_trusted(p)

        resp = await handler.handle_get_capability(StubRequest(name="thread-digest"))

        assert resp.status == 200
        cap = json.loads(resp.text)["capability"]
        assert cap["name"] == "thread-digest"
        assert cap["artifact"] == VALID_CAPABILITY
        assert "reviews" not in cap

    async def test_staging_detail_with_reviews(self, tmp_path: Path):
        handler, p = _handler(tmp_path)
        _stage(p)
        append_staging_review("thread-digest", "VERDICT: approve\n看起来没问题。", p)

        resp = await handler.handle_get_staging_capability(StubRequest(name="thread-digest"))

        assert resp.status == 200
        cap = json.loads(resp.text)["capability"]
        assert cap["trusted"] is False
        assert cap["artifact"] == VALID_CAPABILITY
        assert "VERDICT: approve" in cap["reviews"]

    async def test_trusted_detail_missing_404(self, tmp_path: Path):
        handler, _p = _handler(tmp_path)

        resp = await handler.handle_get_capability(StubRequest(name="nope"))

        assert resp.status == 404
        assert json.loads(resp.text)["reason"] == "capability_not_found"

    async def test_staging_detail_missing_404(self, tmp_path: Path):
        handler, _p = _handler(tmp_path)

        resp = await handler.handle_get_staging_capability(StubRequest(name="nope"))

        assert resp.status == 404

    async def test_detail_unwired_503(self, tmp_path: Path):
        handler, _p = _handler(tmp_path, wire=False)

        assert (await handler.handle_get_capability(StubRequest(name="x"))).status == 503
        assert (await handler.handle_get_staging_capability(StubRequest(name="x"))).status == 503

    async def test_detail_includes_passing_examples(self, tmp_path: Path):
        handler, p = _handler(tmp_path)
        _write_trusted(p, CAPABILITY_WITH_EXAMPLES, name="shout")

        resp = await handler.handle_get_capability(StubRequest(name="shout"))

        assert resp.status == 200
        cap = json.loads(resp.text)["capability"]
        assert cap["examples"] == [
            {"input": '{"s" "hey"}', "expect": '"hey!"', "passed": True, "actual": '"hey!"'}
        ]

    async def test_detail_includes_failed_examples(self, tmp_path: Path):
        """A handwritten trusted artifact with failing assertions (the
        bridge would exclude the word at load; detail stays fail-soft and
        shows the failure as review material): value mismatch carries
        ``actual``, an eval error carries ``error``."""
        handler, p = _handler(tmp_path)
        _write_trusted(p, CAPABILITY_WITH_FAILING_EXAMPLE, name="shout")

        resp = await handler.handle_get_capability(StubRequest(name="shout"))

        assert resp.status == 200
        [mismatch, eval_error] = json.loads(resp.text)["capability"]["examples"]
        assert mismatch["passed"] is False
        assert mismatch["expect"] == "3"
        assert mismatch["actual"] == "2"
        assert "error" not in mismatch
        assert eval_error["passed"] is False
        assert "actual" not in eval_error
        assert "number" in eval_error["error"]

    async def test_detail_without_examples_has_empty_list(self, tmp_path: Path):
        handler, p = _handler(tmp_path)
        _write_trusted(p)

        resp = await handler.handle_get_capability(StubRequest(name="thread-digest"))

        assert json.loads(resp.text)["capability"]["examples"] == []


class TestRunExamplesFacade:
    def test_missing_capability_returns_empty(self, tmp_path: Path):
        facade = CapabilitiesFacade(tlon_paths=TlonPaths(home=tmp_path))
        assert facade.run_examples("nope") == []

    def test_unparseable_artifact_returns_empty(self, tmp_path: Path):
        p = TlonPaths(home=tmp_path)
        _write_trusted(p, "{:version 1 broken", name="broken")
        assert CapabilitiesFacade(tlon_paths=p).run_examples("broken") == []

    def test_examples_evaluated(self, tmp_path: Path):
        p = TlonPaths(home=tmp_path)
        _write_trusted(p, CAPABILITY_WITH_EXAMPLES, name="shout")
        [result] = CapabilitiesFacade(tlon_paths=p).run_examples("shout")
        assert result.passed is True
        assert result.input == {"s": "hey"}
        assert result.expect == "hey!"
        assert result.actual == "hey!"
        assert result.error is None


class TestCreateStagingCapability:
    async def test_create_success_201(self, tmp_path: Path):
        handler, p = _handler(tmp_path)

        resp = await handler.handle_create_staging_capability(
            StubRequest(body={"artifact": VALID_CAPABILITY, "proposal_note": "新增摘要词"})
        )

        assert resp.status == 201
        cap = json.loads(resp.text)["capability"]
        assert cap["name"] == "thread-digest"
        assert cap["trusted"] is False
        # Landed on disk in the staging area.
        assert (p.capabilities_staging_dir() / "thread-digest" / "capability.tlon").exists()

    async def test_create_invalid_400_nothing_on_disk(self, tmp_path: Path):
        handler, p = _handler(tmp_path)

        resp = await handler.handle_create_staging_capability(
            StubRequest(body={"artifact": INVALID_CAPABILITY})
        )

        assert resp.status == 400
        body = json.loads(resp.text)
        assert body["reason"] == "invalid_capability"
        assert "wild" in body["error"] or "jump" in body["error"]
        # Validation precedes any disk write.
        assert not (p.capabilities_staging_dir() / "wild").exists()

    async def test_create_missing_artifact_400(self, tmp_path: Path):
        handler, _p = _handler(tmp_path)

        resp = await handler.handle_create_staging_capability(StubRequest(body={}))

        assert resp.status == 400
        assert json.loads(resp.text)["reason"] == "invalid_capability"

    async def test_create_conflict_with_staging_400(self, tmp_path: Path):
        handler, p = _handler(tmp_path)
        _stage(p)

        resp = await handler.handle_create_staging_capability(
            StubRequest(body={"artifact": VALID_CAPABILITY})
        )

        assert resp.status == 400
        assert "already exists" in json.loads(resp.text)["error"]

    async def test_create_unwired_503(self, tmp_path: Path):
        handler, _p = _handler(tmp_path, wire=False)

        resp = await handler.handle_create_staging_capability(
            StubRequest(body={"artifact": VALID_CAPABILITY})
        )

        assert resp.status == 503


class TestApproveReject:
    async def test_approve_moves_to_trusted(self, tmp_path: Path):
        handler, p = _handler(tmp_path)
        _stage(p)

        resp = await handler.handle_approve_capability(StubRequest(name="thread-digest"))

        assert resp.status == 200
        body = json.loads(resp.text)
        assert body == {"status": "success", "name": "thread-digest", "approved": True}
        # Trusted library gained the word; staging lost it.
        assert (p.capabilities_dir() / "thread-digest" / "capability.tlon").exists()
        assert not (p.capabilities_staging_dir() / "thread-digest").exists()

        list_resp = await handler.handle_list_capabilities(StubRequest())
        names = [c["name"] for c in json.loads(list_resp.text)["capabilities"]]
        assert names == ["thread-digest"]
        staging_resp = await handler.handle_list_staging_capabilities(StubRequest())
        assert json.loads(staging_resp.text)["capabilities"] == []

    async def test_approve_missing_400(self, tmp_path: Path):
        handler, _p = _handler(tmp_path)

        resp = await handler.handle_approve_capability(StubRequest(name="nope"))

        assert resp.status == 400
        assert json.loads(resp.text)["status"] == "error"

    async def test_reject_deletes_staging(self, tmp_path: Path):
        handler, p = _handler(tmp_path)
        _stage(p)

        resp = await handler.handle_reject_capability(StubRequest(name="thread-digest"))

        assert resp.status == 200
        body = json.loads(resp.text)
        assert body == {"status": "success", "name": "thread-digest", "rejected": True}
        assert not (p.capabilities_staging_dir() / "thread-digest").exists()

    async def test_reject_missing_400(self, tmp_path: Path):
        handler, _p = _handler(tmp_path)

        resp = await handler.handle_reject_capability(StubRequest(name="nope"))

        assert resp.status == 400

    async def test_approve_reject_unwired_503(self, tmp_path: Path):
        handler, _p = _handler(tmp_path, wire=False)

        assert (await handler.handle_approve_capability(StubRequest(name="x"))).status == 503
        assert (await handler.handle_reject_capability(StubRequest(name="x"))).status == 503


def test_approval_route_is_canonical_without_staging_alias(tmp_path: Path) -> None:
    handler, _paths = _handler(tmp_path)
    routes = {route.resource.canonical for route in handler.create_app().router.routes()}
    assert "/admin/capabilities/{name}/approve" in routes
    assert "/admin/capabilities/staging/{name}/approve" not in routes


async def test_trusted_edit_requires_reapproval_and_rest_can_reapprove(tmp_path: Path) -> None:
    handler, paths = _handler(tmp_path)
    _write_trusted(paths)
    artifact = paths.capabilities_dir() / "thread-digest/capability.tlon"
    artifact.write_text(VALID_CAPABILITY.replace('"digest:"', '"summary:"'))
    response = await handler.handle_get_capability(StubRequest(name="thread-digest"))
    stale = json.loads(response.text)["capability"]
    assert stale["error"] and stale["trusted"]
    assert (
        await handler.handle_approve_capability(StubRequest(name="thread-digest"))
    ).status == 200
    response = await handler.handle_get_capability(StubRequest(name="thread-digest"))
    assert json.loads(response.text)["capability"]["error"] is None
