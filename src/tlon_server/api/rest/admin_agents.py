"""Admin agent endpoints: /admin/config/agents/* (config listing, hot-reload
adopt, member CRUD — wizard removal, A2/ADR 0006).

Runtime touchpoints go through the tlon.api facades (ADR 0019): members
facade for registry operations and member creation, config facade for
agent.yaml reads/writes.
"""

import uuid
from typing import Any

from aiohttp import web

from tlon.api.members import PrototypeError, StopRejectedError

from .common import RestHandlerBase


class AdminAgentHandlers(RestHandlerBase):
    """Admin agent route handlers, mixed into ``RESTAPIHandler``."""

    async def handle_list_agent_configs(self, request: web.Request) -> web.Response:
        """GET /admin/config/agents - List current agent configurations.

        Returns:
            List of agent configurations (sanitized)
        """
        if not self.configs:
            return self._json_response(
                {"status": "error", "message": "Config manager not available"}, status=503
            )

        agents = []
        for agent in self.configs.list_agent_configs():
            agents.append(
                {
                    "id": agent.id,
                    "name": agent.name,
                    "prototype": agent.prototype,
                }
            )

        return self._json_response({"status": "success", "agents": agents, "count": len(agents)})

    async def handle_reload_agent(self, request: web.Request) -> web.Response:
        """POST /admin/config/agents/{agent_id}/reload - Hot-adopt a new agent.

        Entry point for out-of-process creation (ops admin API writes
        agent.yaml): the registry reloads the config, registers
        the agent, and starts it unless it is lazy-activated.

        Returns:
            200 with {"status", "agent_id", "result"} (result is one of
            "already-known", "config-not-found", "registered-lazy",
            "started", "start-failed"), or 503 if the registry is
            unavailable
        """
        if not self.members:
            return self._json_response(
                {"status": "error", "message": "Agent registry not available"}, status=503
            )

        agent_id = request.match_info["agent_id"]
        conflict = self._member_address_conflict(agent_id)
        if conflict is not None:
            return conflict
        result = await self.members.adopt(agent_id)
        return self._json_response({"status": "success", "agent_id": agent_id, "result": result})

    # ---- Admin: agent CRUD + prototype approval (wizard removal, A2) ----

    def _member_address_conflict(self, agent_id: str) -> web.Response | None:
        """Reject the operator's address before modifying or adopting a member."""
        if agent_id.casefold() == self.identity.require_username().casefold():
            return self._json_response(
                {"error": "Member id conflicts with WebUI identity", "reason": "identity_conflict"},
                status=409,
            )
        return None

    def _require_admin_context(self) -> web.Response | None:
        """Common guard: the members and configs facades must be wired."""
        if self.members is None or self.configs is None:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        return None

    async def handle_create_agent(self, request: web.Request) -> web.Response:
        """POST /admin/config/agents - create a member from a prototype.

        Body: ``{"prototype", "name", "model", "agent_id"?, "params"?,
        "persona"?}``. ``model`` is required (ADR 0016 — member-level LLM
        config, no process-level fallback): ``provider/model`` form, e.g.
        ``deepseek/deepseek-chat``. The behavior comes from the prototype
        (behavior genome); ``persona`` (identity/profile/behavior) is
        optional creation-time seed data (ADR 0006) — afterwards persona
        is member-owned (self-editable via ``updatePersona``, ADR 0024).
        The new member is hot-adopted by the registry.

        Returns:
            200 with the created config; 400 on bad input / prototype
            errors; 503 when the admin context is unavailable.
        """
        guard = self._require_admin_context()
        if guard is not None:
            return guard
        assert self.members is not None
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"status": "error", "message": "invalid JSON body"}, status=400
            )
        prototype: str = str(body.get("prototype") or "")
        name: str = str(body.get("name") or "")
        model: str = str(body.get("model") or "").strip()
        if not prototype or not name:
            return self._json_response(
                {"status": "error", "message": "prototype and name are required"},
                status=400,
            )
        if not model:
            return self._json_response(
                {"status": "error", "message": "model is required"},
                status=400,
            )
        agent_id = str(body.get("agent_id") or f"m-{prototype}-{uuid.uuid4().hex[:8]}")
        conflict = self._member_address_conflict(agent_id)
        if conflict is not None:
            return conflict
        params: dict[str, Any] | None = body.get("params")
        persona: dict[str, Any] = body.get("persona") or {}

        try:
            cfg = self.members.create(
                prototype, agent_id, name, model, params=params, persona=persona
            )
        except PrototypeError as e:
            return self._json_response({"status": "error", "message": str(e)}, status=400)

        result = await self.members.adopt(agent_id)
        return self._json_response(
            {
                "status": "success",
                "agent_id": agent_id,
                "name": cfg.name,
                "prototype": prototype,
                "adopt": result,
            }
        )

    async def handle_update_agent(self, request: web.Request) -> web.Response:
        """PATCH /admin/config/agents/{agent_id} - edit persona / LLM knobs.

        Body: ``{"persona"?: {identity?, profile?, behavior?}, "llm"?:
        {model_name?, embedding_model_name?}}``. Persona files take effect
        immediately (the prompt cache keys on their mtimes); an LLM change
        rewrites ``agent.yaml`` and hot-reloads the config.

        Returns:
            200 with the updated config; 404 unknown agent; 503 admin
            context unavailable.
        """
        guard = self._require_admin_context()
        if guard is not None:
            return guard
        assert self.members is not None and self.configs is not None
        agent_id = request.match_info["agent_id"]
        conflict = self._member_address_conflict(agent_id)
        if conflict is not None:
            return conflict
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"status": "error", "message": "invalid JSON body"}, status=400
            )

        cfg = self.configs.load_agent(agent_id)
        if cfg is None:
            return self._json_response(
                {"status": "error", "message": f"Agent '{agent_id}' not found"},
                status=404,
            )

        persona: dict[str, Any] = body.get("persona") or {}
        self.members.write_persona(agent_id, persona)

        llm: dict[str, Any] | None = body.get("llm")
        if llm:
            updated = self.configs.update_llm(
                agent_id,
                model_name=str(llm["model_name"]) if llm.get("model_name") else None,
                embedding_model_name=(
                    str(llm["embedding_model_name"]) if llm.get("embedding_model_name") else None
                ),
            )
            assert updated is not None  # loaded above
            cfg = updated
            await self.members.adopt(agent_id)  # reload config

        return self._json_response(
            {
                "status": "success",
                "agent_id": agent_id,
                "name": cfg.name,
                "prototype": cfg.prototype,
            }
        )

    async def handle_delete_agent(self, request: web.Request) -> web.Response:
        """DELETE /admin/config/agents/{agent_id} - administratively remove
        a member (wizard removal, ADR 0006).

        The member is stopped (sleeps) if running, unregistered, and its
        home directory (archive) is deleted. This is the explicit
        management action — the runtime never removes members on its own.

        Returns:
            200 removed; 409 unknown agent or rejection (e.g. live
            sessions); 503 admin context unavailable.
        """
        guard = self._require_admin_context()
        if guard is not None:
            return guard
        assert self.members is not None
        agent_id = request.match_info["agent_id"]
        try:
            await self.members.remove(agent_id, removed_by="admin-api")
        except StopRejectedError as e:
            return self._json_response(
                {"status": "error", "reason": e.reason, "message": str(e)},
                status=409,
            )
        return self._json_response({"status": "success", "agent_id": agent_id, "removed": True})
