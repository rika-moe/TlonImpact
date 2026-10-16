"""Admin arena REST endpoints (docs/design/arena.md, issue #29 切片 3/3).

Same in-process pattern as test_rest_admin_api.py: handlers are called
directly with stub requests. The arena facade is real (thin pass-through)
over a fake runner, against a real temp TlonPaths so report/archive reads
exercise the real staging layout.
"""

import json
from pathlib import Path
from typing import Any

from tlon.agents.prototypes.staging import write_staging_prototype
from tlon.api.arena import ArenaError, ArenaFacade
from tlon.api.prototypes import PrototypesFacade
from tlon.arena.runner import ArenaRun, RunStatus
from tlon.base.paths import TlonPaths
from tlon.base.settings import Settings
from tlon_server.api.rest import RESTAPIHandler

_BEHAVIOR = '{:version 1 :rules [{:name "r" :on :boot :actions []}]}\n'


class StubRequest:
    """Minimal stand-in for aiohttp.web.Request."""

    def __init__(
        self,
        body: dict[str, Any] | None = None,
        name: str | None = None,
        run_id: str | None = None,
    ):
        self._body = body
        self.match_info: dict[str, str] = {}
        if name is not None:
            self.match_info["name"] = name
        if run_id is not None:
            self.match_info["run_id"] = run_id

    async def json(self) -> dict[str, Any]:
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeRunner:
    """Runner stand-in: canned runs, records start_run pass-through."""

    def __init__(self, runs: list[ArenaRun] | None = None, error: ArenaError | None = None):
        self._runs = {r.run_id: r for r in runs or []}
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def start_run(
        self,
        prototype_name: str,
        *,
        model: str | None = None,
        params: dict[str, Any] | None = None,
        corpus_size: int | None = None,
        time_box_seconds: float | None = None,
    ) -> ArenaRun:
        if self.error is not None:
            raise self.error
        self.calls.append(
            {
                "prototype_name": prototype_name,
                "model": model,
                "params": params,
                "corpus_size": corpus_size,
                "time_box_seconds": time_box_seconds,
            }
        )
        run = _run(prototype=prototype_name, model=model or "deepseek/deepseek-chat")
        self._runs[run.run_id] = run
        return run

    def get_run(self, run_id: str) -> ArenaRun | None:
        return self._runs.get(run_id)

    def list_runs(self) -> list[ArenaRun]:
        return list(self._runs.values())


def _run(
    run_id: str = "20261005-120000-abc123",
    prototype: str = "staged",
    model: str = "deepseek/deepseek-chat",
    status: RunStatus = RunStatus.RUNNING,
) -> ArenaRun:
    return ArenaRun(
        run_id=run_id,
        prototype_name=prototype,
        model=model,
        params=None,
        corpus_limit=8,
        time_box_seconds=600.0,
        status=status,
        created_at="2026-10-05T12:00:00+00:00",
    )


def _handler(
    tmp_path: Path,
    runner: FakeRunner | None = None,
    wire_arena: bool = True,
) -> tuple[RESTAPIHandler, FakeRunner, TlonPaths]:
    p = TlonPaths(home=tmp_path)
    fake = runner or FakeRunner()
    arena = (
        ArenaFacade(tlon_paths=p, settings=Settings.from_env(), runner_factory=lambda _paths: fake)
        if wire_arena
        else None
    )
    handler = RESTAPIHandler(
        None,
        prototypes=PrototypesFacade(tlon_paths=p),
        arena=arena,
    )
    return handler, fake, p


def _stage(p: TlonPaths, name: str = "staged") -> None:
    write_staging_prototype(name, _BEHAVIOR, author_id="adam", tlon_paths=p)


def _write_archive(
    p: TlonPaths,
    prototype: str,
    run_id: str,
    *,
    with_report: bool = True,
    red_flags: list[dict[str, Any]] | None = None,
) -> Path:
    archive = p.prototypes_staging_dir() / prototype / "arena" / run_id
    archive.mkdir(parents=True)
    run = _run(run_id=run_id, prototype=prototype, status=RunStatus.DONE)
    (archive / "run.json").write_text(
        json.dumps(run.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    if with_report:
        report = {
            "run_id": run_id,
            "prototype": prototype,
            "red_flags": red_flags or [],
        }
        (archive / "report.json").write_text(
            json.dumps(report, ensure_ascii=False), encoding="utf-8"
        )
        (archive / "ARENA.md").write_text("# 演武报告\n\n无红旗。\n", encoding="utf-8")
    return archive


class TestStartArenaRun:
    async def test_trigger_success_202_passthrough(self, tmp_path: Path):
        handler, fake, p = _handler(tmp_path)
        _stage(p)

        resp = await handler.handle_start_arena_run(
            StubRequest(
                body={"model": "deepseek/deepseek-chat", "corpus_size": 10, "params": {"x": 1}},
                name="staged",
            )
        )

        assert resp.status == 202
        body = json.loads(resp.text)
        assert body["status"] == "success"
        assert body["run"]["prototype"] == "staged"
        assert body["run"]["model"] == "deepseek/deepseek-chat"
        assert fake.calls == [
            {
                "prototype_name": "staged",
                "model": "deepseek/deepseek-chat",
                "params": {"x": 1},
                "corpus_size": 10,
                "time_box_seconds": None,
            }
        ]

    async def test_trigger_empty_body_ok(self, tmp_path: Path):
        """body 全可选——空 body 也走缺省解析（TLON_ARENA_MODEL 等）。"""
        handler, fake, p = _handler(tmp_path)
        _stage(p)

        resp = await handler.handle_start_arena_run(StubRequest(name="staged"))

        assert resp.status == 202
        assert fake.calls[0]["model"] is None

    async def test_arena_error_400(self, tmp_path: Path):
        handler, _fake, p = _handler(
            tmp_path, runner=FakeRunner(error=ArenaError("已有活跃演武 run"))
        )
        _stage(p)

        resp = await handler.handle_start_arena_run(StubRequest(body={"model": "m"}, name="staged"))

        assert resp.status == 400
        body = json.loads(resp.text)
        assert body["reason"] == "arena_rejected"
        assert "已有活跃演武 run" in body["error"]

    async def test_invalid_field_types_400(self, tmp_path: Path):
        handler, _fake, p = _handler(tmp_path)
        _stage(p)

        resp = await handler.handle_start_arena_run(
            StubRequest(body={"corpus_size": "ten"}, name="staged")
        )

        assert resp.status == 400
        assert json.loads(resp.text)["reason"] == "invalid_arena_request"

    async def test_unwired_503(self, tmp_path: Path):
        handler, _fake, p = _handler(tmp_path, wire_arena=False)
        _stage(p)

        resp = await handler.handle_start_arena_run(StubRequest(body={}, name="staged"))

        assert resp.status == 503


class TestArenaRunQueries:
    async def test_list_runs(self, tmp_path: Path):
        runs = [
            _run(run_id="20261005-120000-aaa111"),
            _run(run_id="20261005-130000-bbb222", status=RunStatus.DONE),
        ]
        handler, _fake, _p = _handler(tmp_path, runner=FakeRunner(runs=runs))

        resp = await handler.handle_list_arena_runs(StubRequest())

        assert resp.status == 200
        body = json.loads(resp.text)
        assert [r["run_id"] for r in body["runs"]] == [
            "20261005-120000-aaa111",
            "20261005-130000-bbb222",
        ]
        assert body["runs"][1]["status"] == "done"

    async def test_get_run(self, tmp_path: Path):
        handler, _fake, _p = _handler(tmp_path, runner=FakeRunner(runs=[_run()]))

        resp = await handler.handle_get_arena_run(StubRequest(run_id="20261005-120000-abc123"))

        assert resp.status == 200
        assert json.loads(resp.text)["run"]["run_id"] == "20261005-120000-abc123"

    async def test_get_run_unknown_404(self, tmp_path: Path):
        handler, _fake, _p = _handler(tmp_path)

        resp = await handler.handle_get_arena_run(StubRequest(run_id="nope"))

        assert resp.status == 404
        assert json.loads(resp.text)["reason"] == "run_not_found"

    async def test_queries_unwired_503(self, tmp_path: Path):
        handler, _fake, _p = _handler(tmp_path, wire_arena=False)

        assert (await handler.handle_list_arena_runs(StubRequest())).status == 503
        assert (await handler.handle_get_arena_run(StubRequest(run_id="x"))).status == 503
        assert (
            await handler.handle_get_arena_report(StubRequest(name="s", run_id="x"))
        ).status == 503


class TestArenaReport:
    async def test_report_endpoint(self, tmp_path: Path):
        handler, _fake, p = _handler(tmp_path)
        _stage(p)
        flags = [
            {
                "kind": "spawn-attempt",
                "detail": "调用了 spawnAgent",
                "evidence": "sessions/s.jsonl#seq-3",
            }
        ]
        _write_archive(p, "staged", "20261005-120000-abc123", red_flags=flags)

        resp = await handler.handle_get_arena_report(
            StubRequest(name="staged", run_id="20261005-120000-abc123")
        )

        assert resp.status == 200
        body = json.loads(resp.text)
        assert body["report"]["run_id"] == "20261005-120000-abc123"
        assert body["report"]["red_flags"] == flags
        assert body["arena_md"].startswith("# 演武报告")

    async def test_report_missing_404(self, tmp_path: Path):
        handler, _fake, p = _handler(tmp_path)
        _stage(p)

        resp = await handler.handle_get_arena_report(
            StubRequest(name="staged", run_id="20261005-120000-abc123")
        )

        assert resp.status == 404
        assert json.loads(resp.text)["reason"] == "report_not_found"

    async def test_report_run_without_report_404(self, tmp_path: Path):
        """归档目录在但 report.json 未生成（run 进行中/报告失败）→ 404。"""
        handler, _fake, p = _handler(tmp_path)
        _stage(p)
        _write_archive(p, "staged", "20261005-120000-abc123", with_report=False)

        resp = await handler.handle_get_arena_report(
            StubRequest(name="staged", run_id="20261005-120000-abc123")
        )

        assert resp.status == 404


class TestStagingDetailArenaField:
    async def test_detail_with_report(self, tmp_path: Path):
        handler, _fake, p = _handler(tmp_path)
        _stage(p)
        flags = [
            {"kind": "fuel-exhaustion", "detail": "迭代硬顶", "evidence": "sessions/s.jsonl#seq-9"}
        ]
        _write_archive(p, "staged", "20261005-110000-aaa111")
        _write_archive(p, "staged", "20261005-120000-bbb222", red_flags=flags)

        resp = await handler.handle_get_staging_prototype(StubRequest(name="staged"))

        assert resp.status == 200
        arena = json.loads(resp.text)["prototype"]["arena"]
        # run_id 时间序：最新的是 bbb222
        assert arena["latest_run"]["run_id"] == "20261005-120000-bbb222"
        assert arena["latest_run"]["status"] == "done"
        assert arena["red_flags"] == flags

    async def test_detail_without_report(self, tmp_path: Path):
        """无归档目录 → arena 为 None（与 execution-level red_flags 分开字段）。"""
        handler, _fake, p = _handler(tmp_path)
        _stage(p)

        resp = await handler.handle_get_staging_prototype(StubRequest(name="staged"))

        assert resp.status == 200
        proto = json.loads(resp.text)["prototype"]
        assert proto["arena"] is None
        assert proto["red_flags"] == []

    async def test_detail_latest_run_without_report_is_none(self, tmp_path: Path):
        """最新 run 无报告时不冒名旧 run 的报告。"""
        handler, _fake, p = _handler(tmp_path)
        _stage(p)
        _write_archive(p, "staged", "20261005-110000-aaa111")
        _write_archive(p, "staged", "20261005-120000-bbb222", with_report=False)

        resp = await handler.handle_get_staging_prototype(StubRequest(name="staged"))

        assert json.loads(resp.text)["prototype"]["arena"] is None

    async def test_detail_arena_none_when_unwired(self, tmp_path: Path):
        handler, _fake, p = _handler(tmp_path, wire_arena=False)
        _stage(p)
        _write_archive(p, "staged", "20261005-120000-abc123")

        resp = await handler.handle_get_staging_prototype(StubRequest(name="staged"))

        assert json.loads(resp.text)["prototype"]["arena"] is None
