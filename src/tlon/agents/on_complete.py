"""On-complete action execution for Agent (DSL phase 4b).

When a session completes with a final reply, the action forms declared in
``spec.on_complete.actions`` are evaluated once — purely: ``spawn`` /
``emit`` builtins record :class:`ActionRequest` data into a collector
(see ``agents/eval.py``) — and the collected requests are then executed
here, one by one:

- ``spawn`` delegates to ``AgentRegistry.spawn_contractor`` with the
  full trust gate and spawn budget in force (untrusted prototype or
  exhausted budget -> rejection logged, session unaffected). Chained
  spawning (a spawned contractor's own on-complete spawning again) is
  hard-capped by the registry's ``max_spawn_depth``.
- ``emit`` delegates to ``agent._send_message``.

Isolation: an evaluation failure or a single failing action is logged
(``on_complete_eval_failed`` / ``on_complete_action_failed``) and never
interrupts the remaining actions or the session lifecycle.
"""

import uuid
from typing import TYPE_CHECKING, Any

from structlog.contextvars import get_contextvars

from tlon.agents.eval import ActionRequest, EvalError, eval_form, make_actions_builtins
from tlon.base.logger import get_logger

if TYPE_CHECKING:
    from tlon.agents.agent import Agent
    from tlon.agents.session import AgentSession

logger = get_logger(__name__)


class OnCompleteRunner:
    """Evaluates and executes the spec's on-complete action expressions.

    Holds a back-reference to the owning Agent for identity, the behavior
    spec, message sending, and the registry (spawn delegation).
    """

    def __init__(self, agent: "Agent"):
        self._agent = agent

    async def run(self, session: "AgentSession", result: str) -> None:
        """Evaluate the spec's action forms and execute collected actions.

        Called once per session completion, after the interaction is saved
        and before the session is marked completed (still inside the
        session span, so contextvars carry the session id for spawn quota
        attribution). Never raises.
        """
        agent = self._agent
        forms = agent.spec.on_complete.actions
        if not forms:
            return

        env: dict[str, Any] = {
            "msg": session.trigger_message,
            "result": result,
            "self": {"id": agent.id, "name": agent.name},
        }

        collector: list[ActionRequest] = []
        builtins = make_actions_builtins(collector)
        for form in forms:
            try:
                eval_form(form, env, builtins)
            except EvalError as e:
                agent._logger.error(
                    "on_complete_eval_failed", session_id=session.id, error=str(e)
                )
            except Exception as e:
                agent._logger.error(
                    "on_complete_eval_failed",
                    session_id=session.id,
                    error=str(e),
                    exc_info=True,
                )

        if not collector:
            return
        agent._logger.info(
            "on_complete_actions",
            session_id=session.id,
            action_count=len(collector),
            kinds=[request.kind for request in collector],
        )
        for request in collector:
            try:
                await self._execute(request)
            except Exception as e:
                agent._logger.error(
                    "on_complete_action_failed",
                    session_id=session.id,
                    action=request.kind,
                    error=str(e),
                    exc_info=True,
                )

    async def _execute(self, request: ActionRequest) -> None:
        if request.kind == "spawn":
            await self._execute_spawn(request.data)
        elif request.kind == "emit":
            await self._agent._send_message(
                request.data["content"], request.data["mentions"]
            )

    async def _execute_spawn(self, data: dict[str, Any]) -> None:
        agent = self._agent
        registry = agent.registry
        if registry is None:
            raise RuntimeError("spawn action unavailable: agent has no registry")
        prototype = data["prototype"]
        name = data["name"] or f"{prototype}-{uuid.uuid4().hex[:4]}"
        # Bound by Agent._session_span via structlog contextvars; a missing
        # binding buckets context-less spawns of this spawner together.
        session_id = get_contextvars().get("session_id")
        await registry.spawn_contractor(
            prototype=prototype,
            name=name,
            spawner_id=agent.id,
            session_id=str(session_id) if session_id else None,
        )
