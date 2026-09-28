"""On-complete action execution for the unified MemberRuntime.

When a cognitive session completes with a final reply, the action forms
declared on the cognitive rule (``rule.actions``) are evaluated once —
purely: action builtins record :class:`ActionRequest` data into a
collector (see ``agents/vocabulary.py``) — and the collected requests are
then executed by the runtime's unified action executor, one by one. All
action kinds are accepted.

The runner is assembled from capabilities — identity, rule, logger and an
``execute_action`` delegate (the MemberRuntime's unified executor) — with
no host back-reference. Isolation: an evaluation failure or a single
failing action is logged and never interrupts the remaining actions or the
session lifecycle.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog

from tlon.agents.trigger import AgentIdentity
from tlon.agents.vocabulary import make_member_builtins, on_complete_env
from tlon.base.logger import get_logger
from tlon.organization.mail.mail import Mail
from tlosure.builtins import ActionRequest, EvalError, FuelExhaustedError
from tlosure.eval import DEFAULT_FUEL, eval_form

if TYPE_CHECKING:
    from tlon.agents.member_spec import MemberRule
    from tlon.agents.session import AgentSession

logger = get_logger(__name__)

#: The unified action-executor contract: run one collected request,
#: with the session's trigger mail (for emit reply-to / forward source).
ExecuteAction = Callable[[ActionRequest, Mail | None], Awaitable[None]]


class OnCompleteRunner:
    """Evaluates and executes the cognitive rule's completion actions.

    Assembled from capabilities (identity / rule / logger / the runtime's
    unified action executor); no reference to the owning MemberRuntime.
    """

    def __init__(
        self,
        identity: AgentIdentity,
        rule: "MemberRule",
        logger: "structlog.stdlib.BoundLogger",
        execute_action: ExecuteAction,
    ):
        self._identity = identity
        self._rule = rule
        self._logger = logger
        self._execute_action = execute_action

    async def run(self, session: "AgentSession", result: str) -> None:
        """Evaluate the rule's action forms and execute collected actions.

        Called once per session completion, after the interaction is saved
        and before the session is marked completed (still inside the
        session span, so contextvars carry the session id for spawn quota
        attribution). Never raises.
        """
        forms = self._rule.actions
        if not forms:
            return

        env: dict[str, Any] = dict(
            on_complete_env(
                session.trigger_message,
                result,
                member_id=self._identity.id,
                name=self._identity.name,
            )
        )

        collector: list[ActionRequest] = []
        builtins = make_member_builtins(collector=collector)
        # The rule's :fuel caps the completion actions' evaluation; the
        # cognitive gate and completion are temporally separated, so each
        # is capped at the same declared budget.
        action_fuel = self._rule.fuel if self._rule.fuel is not None else DEFAULT_FUEL
        for form in forms:
            try:
                eval_form(form, env, builtins, fuel=action_fuel)
            except FuelExhaustedError as e:
                self._logger.error(
                    "on_complete_fuel_exhausted",
                    session_id=session.id,
                    budget=e.budget,
                    error=str(e),
                )
            except EvalError as e:
                self._logger.error(
                    "on_complete_eval_failed", session_id=session.id, error=str(e)
                )
            except Exception as e:
                self._logger.error(
                    "on_complete_eval_failed",
                    session_id=session.id,
                    error=str(e),
                    exc_info=True,
                )

        if not collector:
            return
        self._logger.info(
            "on_complete_actions",
            session_id=session.id,
            action_count=len(collector),
            kinds=[request.kind for request in collector],
        )
        for request in collector:
            try:
                await self._execute_action(request, session.trigger_message)
            except Exception as e:
                self._logger.error(
                    "on_complete_action_failed",
                    session_id=session.id,
                    action=request.kind,
                    error=str(e),
                    exc_info=True,
                )
