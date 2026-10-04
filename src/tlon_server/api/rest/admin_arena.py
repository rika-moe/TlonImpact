"""Admin arena endpoints: /admin/arena-runs + staging 原型的演武触发与报告
（docs/design/arena.md——演武场由人类专属触发，run 后台推进，报告归档在
staging 原型目录 ``arena/<run-id>/``）。

Runtime touchpoints go through the arena facade (ADR 0019)."""

from typing import Any

from aiohttp import web

from tlon.api.arena import ArenaError

from .common import RestHandlerBase


class AdminArenaHandlers(RestHandlerBase):
    """Admin arena route handlers, mixed into ``RESTAPIHandler``."""

    def _require_arena(self) -> web.Response | None:
        """Common guard: the arena facade must be wired."""
        if self.arena is None:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        return None

    async def handle_start_arena_run(self, request: web.Request) -> web.Response:
        """POST /admin/prototypes/staging/{name}/arena-runs - trigger one
        arena run (human-only; it advances in a background task).

        Body all-optional: {model, params, corpus_size, time_box_seconds}.
        202 with the run record; 400 on ArenaError (staging prototype
        missing, no model configured, corpus/time-box over hard caps, or
        another run already active).
        """
        guard = self._require_arena()
        if guard is not None:
            return guard
        assert self.arena is not None
        name = request.match_info["name"]
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return self._json_response(
                {"error": "Invalid JSON", "reason": "invalid_json"}, status=400
            )
        model = body.get("model")
        params = body.get("params")
        corpus_size = body.get("corpus_size")
        time_box_seconds = body.get("time_box_seconds")
        if (
            (model is not None and not isinstance(model, str))
            or (params is not None and not isinstance(params, dict))
            or (corpus_size is not None and not isinstance(corpus_size, int))
            or (
                time_box_seconds is not None
                and not isinstance(time_box_seconds, (int, float))
            )
        ):
            return self._json_response(
                {
                    "error": "Invalid field types: model str, params object, "
                    "corpus_size int, time_box_seconds number",
                    "reason": "invalid_arena_request",
                },
                status=400,
            )
        try:
            run = await self.arena.start_run(
                name,
                model=model,
                params=params,
                corpus_size=corpus_size,
                time_box_seconds=(
                    float(time_box_seconds) if time_box_seconds is not None else None
                ),
            )
        except ArenaError as e:
            return self._json_response(
                {"error": str(e), "reason": "arena_rejected"}, status=400
            )
        return self._json_response(
            {"status": "success", "run": run.to_dict()}, status=202
        )

    async def handle_list_arena_runs(self, request: web.Request) -> web.Response:
        """GET /admin/arena-runs - all run records (oldest first)."""
        guard = self._require_arena()
        if guard is not None:
            return guard
        assert self.arena is not None
        runs = [run.to_dict() for run in self.arena.list_runs()]
        return self._json_response({"status": "success", "runs": runs})

    async def handle_get_arena_run(self, request: web.Request) -> web.Response:
        """GET /admin/arena-runs/{run_id} - one run's live status (the
        WebUI polls this until a terminal status)."""
        guard = self._require_arena()
        if guard is not None:
            return guard
        assert self.arena is not None
        run_id = request.match_info["run_id"]
        run = self.arena.get_run(run_id)
        if run is None:
            return self._json_response(
                {"error": f"Arena run '{run_id}' not found", "reason": "run_not_found"},
                status=404,
            )
        return self._json_response({"status": "success", "run": run.to_dict()})

    async def handle_get_arena_report(self, request: web.Request) -> web.Response:
        """GET /admin/prototypes/staging/{name}/arena-runs/{run_id}/report -
        the archived report.json + ARENA.md text of one run."""
        guard = self._require_arena()
        if guard is not None:
            return guard
        assert self.arena is not None
        payload: dict[str, Any] | None = self.arena.get_report(
            request.match_info["name"], request.match_info["run_id"]
        )
        if payload is None:
            return self._json_response(
                {"error": "Arena report not found", "reason": "report_not_found"},
                status=404,
            )
        return self._json_response({"status": "success", **payload})
