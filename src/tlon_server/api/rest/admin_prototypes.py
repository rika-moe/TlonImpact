"""Admin prototype endpoints: /admin/prototypes/* — trusted library CRUD,
staging listing/editing, and the approve/reject approval flow.

Runtime touchpoints go through the prototypes facade (ADR 0019)."""

from typing import Any

from aiohttp import web

from tlon.api.prototypes import PrototypeError, PrototypeInfo, parse_execution_level

from .common import RestHandlerBase


def _prototype_payload(
    info: PrototypeInfo,
    behavior: str | None = None,
    lessons: str | None = None,
    reviews: str | None = None,
) -> dict[str, Any]:
    """Serialize a PrototypeInfo for the admin API (list + detail shapes)."""
    payload: dict[str, Any] = {
        "name": info.name,
        "display_name": info.display_name,
        "description": info.description,
        "trusted": info.trusted,
        "author": info.author,
        "parent": info.parent,
        "generation": info.generation,
        "proposal_note": info.proposal_note,
        "parameters": [
            {"name": s.name, "type": s.type, "required": s.required} for s in info.parameters
        ],
    }
    if behavior is not None:
        payload["behavior"] = behavior
        # Derived review data (constitution 1, docs/design/execution-device.md):
        # the declared execution level, plus red flags for the human
        # approver — a staging proposal declaring L2 (full Linux userland)
        # is a red flag by itself.
        level = parse_execution_level(behavior)
        payload["execution_level"] = level
        payload["red_flags"] = ["execution-level-l2"] if not info.trusted and level == "L2" else []
    if lessons is not None:
        payload["lessons"] = lessons
    if reviews is not None:
        payload["reviews"] = reviews
    return payload


class AdminPrototypeHandlers(RestHandlerBase):
    """Admin prototype route handlers, mixed into ``RESTAPIHandler``."""

    def _require_prototypes(self) -> web.Response | None:
        """Common guard: the prototypes facade must be wired."""
        if self.prototypes is None:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        return None

    async def handle_list_staging(self, request: web.Request) -> web.Response:
        """GET /admin/prototypes/staging - list LLM-authored prototypes
        awaiting human approval."""
        guard = self._require_prototypes()
        if guard is not None:
            return guard
        assert self.prototypes is not None
        protos = self.prototypes.list_staging()
        return self._json_response(
            {
                "status": "success",
                "prototypes": [_prototype_payload(p) for p in protos],
            }
        )

    async def handle_list_prototypes(self, request: web.Request) -> web.Response:
        """GET /admin/prototypes - list trusted prototypes (behavior genome)
        a member can be created from (wizard removal: ops picks one to
        instantiate)."""
        guard = self._require_prototypes()
        if guard is not None:
            return guard
        assert self.prototypes is not None
        protos = self.prototypes.list_trusted()
        return self._json_response(
            {
                "status": "success",
                "prototypes": [_prototype_payload(p) for p in protos],
            }
        )

    async def handle_get_prototype(self, request: web.Request) -> web.Response:
        """GET /admin/prototypes/{name} - trusted-library prototype detail:
        metadata + parameters + the full behavior.tlon text (+ LESSONS.md
        text when the prototype carries one, issue ; + REVIEW.md peer
        review records when present, issue )."""
        return self._handle_get_detail(request, staging=False)

    async def handle_get_staging_prototype(self, request: web.Request) -> web.Response:
        """GET /admin/prototypes/staging/{name} - staged prototype detail
        (same shape, trusted=false)."""
        return self._handle_get_detail(request, staging=True)

    def _handle_get_detail(self, request: web.Request, staging: bool) -> web.Response:
        guard = self._require_prototypes()
        if guard is not None:
            return guard
        assert self.prototypes is not None
        name = request.match_info["name"]
        try:
            info, behavior, lessons, reviews = self.prototypes.get_detail(name, staging=staging)
        except PrototypeError as e:
            return self._json_response(
                {"error": str(e), "reason": "prototype_not_found"}, status=404
            )
        payload = _prototype_payload(info, behavior, lessons, reviews)
        # Lineage (see docs/design/prototypes.md): the derived parent chain, including per-generation
        # diffs — first-class data on the detail endpoint.
        payload["lineage"] = self.prototypes.get_lineage(name)
        # 演武场（docs/design/arena.md）：staging 详情携带最新 run 摘要 +
        # 报告红旗（与 execution-level 的 red_flags 分开字段，不混）；
        # 无报告或 facade 未接线时为 None。证据呈现，不进 approve 逻辑。
        if staging:
            payload["arena"] = self.arena.latest_report(name) if self.arena else None
        return self._json_response({"status": "success", "prototype": payload})

    async def handle_create_prototype(self, request: web.Request) -> web.Response:
        """POST /admin/prototypes - human-authored prototype, written
        directly into the trusted library (handwritten implies trusted).

        Body: {behavior, display_name, description?, lessons?}. 201 on
        success; 400 invalid_prototype on missing fields or a spec that
        fails the validation chain (nothing is written in that case).
        """
        guard = self._require_prototypes()
        if guard is not None:
            return guard
        assert self.prototypes is not None
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"error": "Invalid JSON", "reason": "invalid_json"}, status=400
            )
        if "name" in body:
            return self._json_response(
                {"error": "Prototype ID is generated by the system", "reason": "managed_id"},
                status=400,
            )
        display_name = body.get("display_name")
        behavior = body.get("behavior")
        if (
            not isinstance(display_name, str)
            or not display_name.strip()
            or not isinstance(behavior, str)
            or not behavior.strip()
        ):
            return self._json_response(
                {
                    "error": "Fields 'display_name' and 'behavior' are required",
                    "reason": "invalid_prototype",
                },
                status=400,
            )
        try:
            info = self.prototypes.create(
                behavior,
                display_name=display_name.strip(),
                description=str(body.get("description") or ""),
                parent=str(body["parent"]) if body.get("parent") else None,
                proposal_note=(str(body["proposal_note"]) if body.get("proposal_note") else None),
                lessons=str(body["lessons"]) if body.get("lessons") else None,
            )
        except PrototypeError as e:
            return self._json_response({"error": str(e), "reason": "invalid_prototype"}, status=400)
        return self._json_response(
            {"status": "success", "prototype": _prototype_payload(info)}, status=201
        )

    async def handle_update_prototype(self, request: web.Request) -> web.Response:
        """PATCH /admin/prototypes/{name} - update metadata and/or behavior
        of a trusted prototype (behavior changes rerun the validation
        chain; a failure leaves the disk untouched)."""
        return await self._handle_update(request, staging=False)

    async def handle_update_staging_prototype(self, request: web.Request) -> web.Response:
        """PATCH /admin/prototypes/staging/{name} - pre-approval editing of
        an LLM-authored prototype (same semantics)."""
        return await self._handle_update(request, staging=True)

    async def _handle_update(self, request: web.Request, staging: bool) -> web.Response:
        guard = self._require_prototypes()
        if guard is not None:
            return guard
        assert self.prototypes is not None
        name = request.match_info["name"]
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"error": "Invalid JSON", "reason": "invalid_json"}, status=400
            )
        try:
            self.prototypes.get_detail(name, staging=staging)
        except PrototypeError as e:
            return self._json_response(
                {"error": str(e), "reason": "prototype_not_found"}, status=404
            )
        display_name = body.get("display_name")
        description = body.get("description")
        behavior = body.get("behavior")
        parent = body.get("parent")
        proposal_note = body.get("proposal_note")
        lessons = body.get("lessons")
        if (
            display_name is None
            and description is None
            and behavior is None
            and parent is None
            and proposal_note is None
            and lessons is None
        ):
            return self._json_response(
                {
                    "error": "Nothing to update: provide display_name, description, behavior, "
                    "parent, proposal_note or lessons",
                    "reason": "invalid_prototype",
                },
                status=400,
            )
        try:
            info = self.prototypes.update(
                name,
                display_name=str(display_name) if display_name is not None else None,
                description=str(description) if description is not None else None,
                behavior=str(behavior) if behavior is not None else None,
                parent=str(parent) if parent else None,
                proposal_note=str(proposal_note) if proposal_note else None,
                lessons=str(lessons) if lessons else None,
                staging=staging,
            )
        except PrototypeError as e:
            return self._json_response({"error": str(e), "reason": "invalid_prototype"}, status=400)
        return self._json_response({"status": "success", "prototype": _prototype_payload(info)})

    async def handle_delete_prototype(self, request: web.Request) -> web.Response:
        """DELETE /admin/prototypes/{name} - remove a prototype from the
        trusted library (staging removal is the existing reject)."""
        guard = self._require_prototypes()
        if guard is not None:
            return guard
        assert self.prototypes is not None
        name = request.match_info["name"]
        try:
            self.prototypes.delete(name)
        except PrototypeError as e:
            return self._json_response(
                {"error": str(e), "reason": "prototype_not_found"}, status=404
            )
        return self._json_response({"status": "success"})

    async def handle_approve_prototype(self, request: web.Request) -> web.Response:
        """POST /admin/prototypes/staging/{name}/approve - promote a staged
        prototype to the trusted library (human approval)."""
        guard = self._require_prototypes()
        if guard is not None:
            return guard
        assert self.prototypes is not None
        name = request.match_info["name"]
        try:
            self.prototypes.approve(name)
        except PrototypeError as e:
            return self._json_response({"status": "error", "message": str(e)}, status=400)
        return self._json_response({"status": "success", "name": name, "approved": True})

    async def handle_reject_prototype(self, request: web.Request) -> web.Response:
        """POST /admin/prototypes/staging/{name}/reject - delete a staged
        prototype (human rejection)."""
        guard = self._require_prototypes()
        if guard is not None:
            return guard
        assert self.prototypes is not None
        name = request.match_info["name"]
        try:
            self.prototypes.reject(name)
        except PrototypeError as e:
            return self._json_response({"status": "error", "message": str(e)}, status=400)
        return self._json_response({"status": "success", "name": name, "rejected": True})
