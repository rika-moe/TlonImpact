"""Admin agent endpoints: /admin/config/agents/* (config listing, hot-reload
adopt, member CRUD — wizard removal, A2/ADR 0006).

Runtime touchpoints go through the tlon.api facades (ADR 0019): members
facade for registry operations and member creation, config facade for
agent.yaml reads/writes.
"""

from typing import Any

from aiohttp import web

from tlon.api.members import PrototypeError, StopRejectedError

from .common import RestHandlerBase


def _validate_self_file_body(body: Any) -> dict[str, str]:
    """Validate self-file input before any files or runtime settings are changed."""
    if not isinstance(body, dict):
        raise ValueError("JSON body must be an object")
    persona = body.get("persona", {})
    if not isinstance(persona, dict):
        raise ValueError("persona must be an object")
    if persona.keys() - {"identity", "profile", "behavior"}:
        raise ValueError("persona fields must be identity, profile or behavior")
    if any(not isinstance(value, str) for value in persona.values()):
        raise ValueError("persona values must be strings")
    llm = body.get("llm", {})
    if not isinstance(llm, dict):
        raise ValueError("llm must be an object")
    if any(not isinstance(value, str) for value in llm.values()):
        raise ValueError("llm values must be strings")
    return persona


class AdminAgentHandlers(RestHandlerBase):
    """Admin agent route handlers, mixed into ``RESTAPIHandler``."""

    async def handle_terminate_agent(self, request: web.Request) -> web.Response:
        """POST /admin/agents/{agent_id}/terminate — drain, then permanently exit.

        This management action preserves history. It never cancels active work.
        """
        if not self.members:
            return self._json_response({"error": "Agent registry not available"}, status=503)
        agent_id = request.match_info["agent_id"]
        if self.members.get_config(agent_id) is None:
            return self._json_response({"error": f"Agent '{agent_id}' not found"}, status=404)
        try:
            terminated = await self.members.terminate(agent_id, stopped_by="admin-api")
        except StopRejectedError as exc:
            return self._json_response({"error": str(exc), "reason": exc.reason}, status=409)
        return self._json_response(
            {
                "status": "success" if terminated else "pending",
                "agent_id": agent_id,
                "stop_pending": not terminated,
                "agent": await self.members.get_status(agent_id),
            },
            status=200 if terminated else 202,
        )

    async def handle_wake_agent(self, request: web.Request) -> web.Response:
        """End timed sleep early; actual unread mail determines whether work starts."""
        return await self._handle_scheduling_action(request, "wake")

    async def handle_wait_agent(self, request: web.Request) -> web.Response:
        """Drain current work and return to normal mailbox-driven waiting."""
        return await self._handle_scheduling_action(request, "wait")

    async def handle_sleep_agent(self, request: web.Request) -> web.Response:
        """Drain current work and sleep until a future timezone-aware deadline."""
        return await self._handle_scheduling_action(request, "sleep")

    async def _handle_scheduling_action(self, request: web.Request, action: str) -> web.Response:
        """Keep management validation and authoritative response semantics aligned."""
        if self.members is None:
            return self._json_response({"error": "Agent registry not available"}, status=503)
        agent_id = request.match_info["agent_id"]
        if self.members.get_config(agent_id) is None:
            return self._json_response({"error": f"Agent '{agent_id}' not found"}, status=404)
        try:
            if action == "sleep":
                body = await request.json()
                if (
                    not isinstance(body, dict)
                    or set(body) != {"wake_at"}
                    or not isinstance(body["wake_at"], str)
                ):
                    raise ValueError("body must contain only a string wake_at")
                completed = await self.members.sleep(agent_id, body["wake_at"])
            elif action == "wake":
                completed = await self.members.wake(agent_id)
            else:
                completed = await self.members.wait(agent_id)
        except StopRejectedError as exc:
            return self._json_response(
                {"error": str(exc), "reason": exc.reason},
                status=404 if exc.reason == "unknown_agent" else 409,
            )
        except ValueError as exc:
            return self._json_response({"error": str(exc)}, status=400)
        status = await self.members.get_status(agent_id)
        return self._json_response(
            {
                "status": "success" if completed else "pending",
                "agent_id": agent_id,
                "scheduling_pending": status["scheduling_pending"] if status else None,
                "stop_pending": status["stop_pending"] if status else False,
                "agent": status,
            },
            status=200 if completed else 202,
        )

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

        Body: ``{"prototype", "name", "model", "params"?,
        "persona"?}``. ``model`` is required (ADR 0016 — member-level LLM
        config, no process-level fallback): ``provider/model`` form, e.g.
        ``deepseek/deepseek-chat``. The behavior comes from the prototype
        (behavior genome); ``persona`` (identity/profile/behavior) is
        optional creation-time seed data (ADR 0006). IDENTITY and SOUL
        are self-editable via ``persona-update`` (ADR 0024); RULES is
        maintained by humans.
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
        try:
            persona = _validate_self_file_body(body)
        except ValueError as error:
            return self._json_response({"status": "error", "message": str(error)}, status=400)
        if "agent_id" in body:
            return self._json_response(
                {"error": "Member ID is generated by the system", "reason": "managed_id"},
                status=400,
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
        params: dict[str, Any] | None = body.get("params")

        try:
            cfg = self.members.create(
                prototype,
                name,
                model,
                params=params,
                persona=persona,
                reserved_ids=(self.identity.require_username(),),
            )
        except PrototypeError as e:
            return self._json_response({"status": "error", "message": str(e)}, status=400)

        agent_id = cfg.id
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

    async def handle_get_agent_config(self, request: web.Request) -> web.Response:
        """GET /admin/config/agents/{agent_id} - read self files and the current model."""
        guard = self._require_admin_context()
        if guard is not None:
            return guard
        assert self.members is not None and self.configs is not None
        agent_id = request.match_info["agent_id"]
        cfg = self.configs.load_agent(agent_id)
        if cfg is None:
            return self._json_response(
                {"status": "error", "message": f"Agent '{agent_id}' not found"}, status=404
            )
        return self._json_response(
            {
                "status": "success",
                "agent_id": agent_id,
                "persona": self.members.read_persona(agent_id),
                "llm": {"model_name": cfg.llm.model_name if cfg.llm else ""},
            }
        )

    async def handle_update_agent(self, request: web.Request) -> web.Response:
        """PATCH /admin/config/agents/{agent_id} - edit persona / LLM knobs.

        Body: ``{"persona"?: {identity?, profile?, behavior?}, "llm"?:
        {model_name?, embedding_model_name?}}``. Persona files take effect
        on the next prompt build (the cache keys on file mtimes). Omitted
        persona fields stay unchanged; empty strings clear the supplied files.
        An LLM change
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

        try:
            persona = _validate_self_file_body(body)
        except ValueError as error:
            return self._json_response({"status": "error", "message": str(error)}, status=400)
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

        The member is terminated if running, unregistered, and its
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
