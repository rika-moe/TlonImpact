"""Admin prototype endpoints: /admin/prototypes/* — trusted library CRUD,
staging listing/editing, and the approve/reject approval flow."""

from typing import Any

from aiohttp import web

from tlon.agents.prototypes import (
    PrototypeInfo,
    approve_prototype,
    delete_prototype,
    get_prototype_detail,
    get_prototype_lineage,
    list_prototypes,
    list_staging_prototypes,
    reject_prototype,
    update_prototype,
    write_trusted_prototype,
)
from tlon.tools.prototypes.ports import PrototypeError

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
            {"name": s.name, "type": s.type, "required": s.required}
            for s in info.parameters
        ],
    }
    if behavior is not None:
        payload["behavior"] = behavior
    if lessons is not None:
        payload["lessons"] = lessons
    if reviews is not None:
        payload["reviews"] = reviews
    return payload


class AdminPrototypeHandlers(RestHandlerBase):
    """Admin prototype route handlers, mixed into ``RESTAPIHandler``."""

    async def handle_list_staging(self, request: web.Request) -> web.Response:
        """GET /admin/prototypes/staging - list LLM-authored prototypes
        awaiting human approval."""
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        protos = list_staging_prototypes(self.paths)
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
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        protos = list_prototypes(self.paths)
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
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        name = request.match_info["name"]
        try:
            info, behavior, lessons, reviews = get_prototype_detail(
                name, staging=staging, tlon_paths=self.paths
            )
        except PrototypeError as e:
            return self._json_response(
                {"error": str(e), "reason": "prototype_not_found"}, status=404
            )
        payload = _prototype_payload(info, behavior, lessons, reviews)
        # Lineage (): the derived parent chain, including per-generation
        # diffs — first-class data on the detail endpoint.
        payload["lineage"] = get_prototype_lineage(name, tlon_paths=self.paths)
        return self._json_response({"status": "success", "prototype": payload})

    async def handle_create_prototype(self, request: web.Request) -> web.Response:
        """POST /admin/prototypes - human-authored prototype, written
        directly into the trusted library (handwritten implies trusted).

        Body: {name, behavior, display_name?, description?, lessons?}. 201 on
        success; 400 invalid_prototype on missing fields or a spec that
        fails the validation chain (nothing is written in that case).
        """
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"error": "Invalid JSON", "reason": "invalid_json"}, status=400
            )
        name = body.get("name")
        behavior = body.get("behavior")
        if not name or not isinstance(name, str) or not behavior or not isinstance(behavior, str):
            return self._json_response(
                {"error": "Fields 'name' and 'behavior' are required",
                 "reason": "invalid_prototype"},
                status=400,
            )
        try:
            info = write_trusted_prototype(
                name,
                behavior,
                display_name=str(body.get("display_name") or ""),
                description=str(body.get("description") or ""),
                parent=str(body["parent"]) if body.get("parent") else None,
                proposal_note=(
                    str(body["proposal_note"]) if body.get("proposal_note") else None
                ),
                lessons=str(body["lessons"]) if body.get("lessons") else None,
                tlon_paths=self.paths,
            )
        except PrototypeError as e:
            return self._json_response(
                {"error": str(e), "reason": "invalid_prototype"}, status=400
            )
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
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        name = request.match_info["name"]
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"error": "Invalid JSON", "reason": "invalid_json"}, status=400
            )
        try:
            get_prototype_detail(name, staging=staging, tlon_paths=self.paths)
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
                {"error": "Nothing to update: provide display_name, description, behavior, "
                          "parent, proposal_note or lessons",
                 "reason": "invalid_prototype"},
                status=400,
            )
        try:
            info = update_prototype(
                name,
                display_name=str(display_name) if display_name is not None else None,
                description=str(description) if description is not None else None,
                behavior_edn=str(behavior) if behavior is not None else None,
                parent=str(parent) if parent else None,
                proposal_note=str(proposal_note) if proposal_note else None,
                lessons=str(lessons) if lessons is not None else None,
                staging=staging,
                tlon_paths=self.paths,
            )
        except PrototypeError as e:
            return self._json_response(
                {"error": str(e), "reason": "invalid_prototype"}, status=400
            )
        return self._json_response(
            {"status": "success", "prototype": _prototype_payload(info)}
        )

    async def handle_delete_prototype(self, request: web.Request) -> web.Response:
        """DELETE /admin/prototypes/{name} - remove a prototype from the
        trusted library (staging removal is the existing reject)."""
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        name = request.match_info["name"]
        try:
            delete_prototype(name, self.paths)
        except PrototypeError as e:
            return self._json_response(
                {"error": str(e), "reason": "prototype_not_found"}, status=404
            )
        return self._json_response({"status": "success"})

    async def handle_approve_prototype(self, request: web.Request) -> web.Response:
        """POST /admin/prototypes/staging/{name}/approve - promote a staged
        prototype to the trusted library (human approval)."""
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        name = request.match_info["name"]
        try:
            approve_prototype(name, self.paths)
        except PrototypeError as e:
            return self._json_response({"status": "error", "message": str(e)}, status=400)
        return self._json_response({"status": "success", "name": name, "approved": True})

    async def handle_reject_prototype(self, request: web.Request) -> web.Response:
        """POST /admin/prototypes/staging/{name}/reject - delete a staged
        prototype (human rejection)."""
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        name = request.match_info["name"]
        try:
            reject_prototype(name, self.paths)
        except PrototypeError as e:
            return self._json_response({"status": "error", "message": str(e)}, status=400)
        return self._json_response({"status": "success", "name": name, "rejected": True})
