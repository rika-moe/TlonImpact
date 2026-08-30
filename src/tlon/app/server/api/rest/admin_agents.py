"""Admin agent endpoints: /admin/config/agents/* (config listing, hot-reload
adopt, member CRUD — wizard removal, A2/ADR 0006)."""

import uuid
from typing import Any

from aiohttp import web

from tlon.agents.prototypes import instantiate
from tlon.base.config import yaml_load_file, yaml_save
from tlon.tools.agents.ports import StopRejectedError
from tlon.tools.prototypes.ports import PrototypeError

from .common import RestHandlerBase


class AdminAgentHandlers(RestHandlerBase):
    """Admin agent route handlers, mixed into ``RESTAPIHandler``."""

    async def handle_list_agent_configs(self, request: web.Request) -> web.Response:
        """GET /admin/config/agents - List current agent configurations.

        Returns:
            List of agent configurations (sanitized)
        """
        if not self.config_manager:
            return self._json_response(
                {"status": "error", "message": "Config manager not available"}, status=503
            )

        agents = []
        for agent in self.config_manager.agents.values():
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
        if not self.agent_registry:
            return self._json_response(
                {"status": "error", "message": "Agent registry not available"}, status=503
            )

        agent_id = request.match_info["agent_id"]
        result = await self.agent_registry.adopt_agent(agent_id)
        return self._json_response(
            {"status": "success", "agent_id": agent_id, "result": result}
        )

    # ---- Admin: agent CRUD + prototype approval (wizard removal, A2) ----

    def _require_admin_context(self) -> web.Response | None:
        """Common guard: registry + paths must be available."""
        if not self.agent_registry or not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        return None

    async def handle_create_agent(self, request: web.Request) -> web.Response:
        """POST /admin/config/agents - create a member from a prototype.

        Body: ``{"prototype", "name", "agent_id"?, "params"?, "persona"?}``.
        The behavior comes from the prototype (behavior genome); ``persona``
        (identity/profile/behavior) is optional creation-time data (ADR
        0006). The new member is hot-adopted by the registry.

        Returns:
            200 with the created config; 400 on bad input / prototype
            errors; 503 when the admin context is unavailable.
        """
        guard = self._require_admin_context()
        if guard is not None:
            return guard
        paths = self.paths
        assert paths is not None and self.agent_registry is not None
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"status": "error", "message": "invalid JSON body"}, status=400
            )
        prototype: str = str(body.get("prototype") or "")
        name: str = str(body.get("name") or "")
        if not prototype or not name:
            return self._json_response(
                {"status": "error", "message": "prototype and name are required"},
                status=400,
            )
        agent_id = str(body.get("agent_id") or f"m-{prototype}-{uuid.uuid4().hex[:8]}")
        params: dict[str, Any] | None = body.get("params")
        persona: dict[str, Any] = body.get("persona") or {}

        try:
            cfg = instantiate(
                prototype, agent_id, name, params=params, tlon_paths=paths
            )
        except PrototypeError as e:
            return self._json_response({"status": "error", "message": str(e)}, status=400)

        config_dir = paths.agent_config_dir(agent_id)
        for field, filename in (
            ("identity", "IDENTITY.md"),
            ("profile", "SOUL.md"),
            ("behavior", "RULES.md"),
        ):
            content = persona.get(field)
            if content:
                (config_dir / filename).write_text(
                    str(content).strip() + "\n", encoding="utf-8"
                )

        result = await self.agent_registry.adopt_agent(agent_id)
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
        {model_name?, temperature?}}``. Persona files take effect
        immediately (the prompt cache keys on their mtimes); an LLM change
        rewrites ``agent.yaml`` and hot-reloads the config.

        Returns:
            200 with the updated config; 404 unknown agent; 503 admin
            context unavailable.
        """
        guard = self._require_admin_context()
        if guard is not None:
            return guard
        paths = self.paths
        assert paths is not None and self.agent_registry is not None
        agent_id = request.match_info["agent_id"]
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"status": "error", "message": "invalid JSON body"}, status=400
            )

        config_file = paths.agent_config_file(agent_id)
        cfg = yaml_load_file(config_file)
        if cfg is None:
            return self._json_response(
                {"status": "error", "message": f"Agent '{agent_id}' not found"},
                status=404,
            )

        config_dir = paths.agent_config_dir(agent_id)
        persona: dict[str, Any] = body.get("persona") or {}
        for field, filename in (
            ("identity", "IDENTITY.md"),
            ("profile", "SOUL.md"),
            ("behavior", "RULES.md"),
        ):
            content = persona.get(field)
            if content:
                (config_dir / filename).write_text(
                    str(content).strip() + "\n", encoding="utf-8"
                )

        llm: dict[str, Any] | None = body.get("llm")
        if llm:
            # model_copy preserves the untouched knobs (max_tokens / top_p /
            # an explicit context_window override); a fresh LLMConfig would
            # silently reset them to defaults.
            cfg.llm = cfg.llm.model_copy(
                update={
                    "model_name": str(llm.get("model_name") or cfg.llm.model_name),
                    "temperature": float(llm.get("temperature") or cfg.llm.temperature),
                }
            )
            yaml_save(cfg, paths.tlon_home())
            await self.agent_registry.adopt_agent(agent_id)  # reload config

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
        agent_id = request.match_info["agent_id"]
        try:
            await self.agent_registry.remove_member(agent_id, removed_by="admin-api")
        except StopRejectedError as e:
            return self._json_response(
                {"status": "error", "reason": e.reason, "message": str(e)},
                status=409,
            )
        return self._json_response(
            {"status": "success", "agent_id": agent_id, "removed": True}
        )
