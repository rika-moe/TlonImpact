"""Admin capability endpoints: /admin/capabilities/* — trusted-library
listing/detail and the staging channel (create/list/detail/approve/reject)
for capability artifacts (docs/design/capabilities.md, issue #45).

Runtime touchpoints go through the capabilities facade (ADR 0019)."""

from typing import Any

from aiohttp import web

from tlon.api.capabilities import CapabilityError, CapabilityInfo, example_result_payload

from .common import RestHandlerBase


def _capability_payload(
    info: CapabilityInfo,
    artifact: str | None = None,
    reviews: str | None = None,
    examples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Serialize a CapabilityInfo for the admin API (list + detail shapes)."""
    payload: dict[str, Any] = {
        "name": info.name,
        "description": info.description,
        "pure": info.pure,
        "trusted": info.trusted,
        "author": info.author,
        "proposal_note": info.proposal_note,
        "deprecated": info.deprecated,
    }
    if artifact is not None:
        payload["artifact"] = artifact
    if reviews is not None:
        payload["reviews"] = reviews
    if examples is not None:
        payload["examples"] = examples
    return payload


class AdminCapabilityHandlers(RestHandlerBase):
    """Admin capability route handlers, mixed into ``RESTAPIHandler``."""

    def _require_capabilities(self) -> web.Response | None:
        """Common guard: the capabilities facade must be wired."""
        if self.capabilities is None:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        return None

    async def handle_list_capabilities(self, request: web.Request) -> web.Response:
        """GET /admin/capabilities - list the trusted capability library."""
        guard = self._require_capabilities()
        if guard is not None:
            return guard
        assert self.capabilities is not None
        caps = self.capabilities.list_trusted()
        return self._json_response(
            {"status": "success", "capabilities": [_capability_payload(c) for c in caps]}
        )

    async def handle_list_staging_capabilities(self, request: web.Request) -> web.Response:
        """GET /admin/capabilities/staging - list capabilities awaiting
        human approval."""
        guard = self._require_capabilities()
        if guard is not None:
            return guard
        assert self.capabilities is not None
        caps = self.capabilities.list_staging()
        return self._json_response(
            {"status": "success", "capabilities": [_capability_payload(c) for c in caps]}
        )

    async def handle_get_capability(self, request: web.Request) -> web.Response:
        """GET /admin/capabilities/{name} - trusted-library capability
        detail: metadata + the full capability.tlon artifact text
        (+ :examples assertion outcomes + REVIEW.md records when present)."""
        return self._handle_get_capability_detail(request, staging=False)

    async def handle_get_staging_capability(self, request: web.Request) -> web.Response:
        """GET /admin/capabilities/staging/{name} - staged capability
        detail (same shape, trusted=false)."""
        return self._handle_get_capability_detail(request, staging=True)

    def _handle_get_capability_detail(self, request: web.Request, staging: bool) -> web.Response:
        guard = self._require_capabilities()
        if guard is not None:
            return guard
        assert self.capabilities is not None
        name = request.match_info["name"]
        try:
            info, artifact, reviews = self.capabilities.get_detail(name, staging=staging)
        except CapabilityError as e:
            return self._json_response(
                {"error": str(e), "reason": "capability_not_found"}, status=404
            )
        examples = [
            example_result_payload(r) for r in self.capabilities.run_examples(name, staging=staging)
        ]
        return self._json_response(
            {"status": "success", "capability": _capability_payload(info, artifact, reviews, examples)}
        )

    async def handle_create_staging_capability(self, request: web.Request) -> web.Response:
        """POST /admin/capabilities/staging - stage a capability artifact
        (human-authored via the WebUI form in v1; validation precedes any
        disk write).

        Body: {artifact, proposal_note?}. The artifact's :name is the
        capability's identity. 201 on success; 400 invalid_capability on
        missing fields or a CapabilityError (nothing is written then).
        """
        guard = self._require_capabilities()
        if guard is not None:
            return guard
        assert self.capabilities is not None
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"error": "Invalid JSON", "reason": "invalid_json"}, status=400
            )
        artifact = body.get("artifact") if isinstance(body, dict) else None
        if not artifact or not isinstance(artifact, str):
            return self._json_response(
                {"error": "Field 'artifact' (capability EDN text) is required",
                 "reason": "invalid_capability"},
                status=400,
            )
        proposal_note = body.get("proposal_note")
        try:
            info = self.capabilities.create_staging(
                artifact,
                proposal_note=str(proposal_note) if proposal_note else None,
            )
        except CapabilityError as e:
            return self._json_response(
                {"error": str(e), "reason": "invalid_capability"}, status=400
            )
        return self._json_response(
            {"status": "success", "capability": _capability_payload(info)}, status=201
        )

    async def handle_approve_capability(self, request: web.Request) -> web.Response:
        """POST /admin/capabilities/staging/{name}/approve - promote a
        staged capability to the trusted library (human approval; members
        pick the word up on next wake via the vocabulary bridge)."""
        guard = self._require_capabilities()
        if guard is not None:
            return guard
        assert self.capabilities is not None
        name = request.match_info["name"]
        try:
            self.capabilities.approve(name)
        except CapabilityError as e:
            return self._json_response({"status": "error", "message": str(e)}, status=400)
        return self._json_response({"status": "success", "name": name, "approved": True})

    async def handle_reject_capability(self, request: web.Request) -> web.Response:
        """POST /admin/capabilities/staging/{name}/reject - delete a staged
        capability (human rejection)."""
        guard = self._require_capabilities()
        if guard is not None:
            return guard
        assert self.capabilities is not None
        name = request.match_info["name"]
        try:
            self.capabilities.reject(name)
        except CapabilityError as e:
            return self._json_response({"status": "error", "message": str(e)}, status=400)
        return self._json_response({"status": "success", "name": name, "rejected": True})
