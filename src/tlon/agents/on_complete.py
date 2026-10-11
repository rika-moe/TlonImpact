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
from tlosure.eval import DEFAULT_FUEL, DEFAULT_MAX_DEPTH, Evaluator

if TYPE_CHECKING:
    from tlon.agents.member_spec import MemberRule
    from tlon.agents.session import AgentSession

logger = get_logger(__name__)

#: The unified action-executor contract: run one collected request. The
#: second argument was the session's trigger mail (emit reply-to / forward
#: source); ADR 0028 retired the trigger mail, so it is always None now.
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
                # ADR 0028: the trigger mail is gone, so ``msg`` is always
                # None on this path (the env shape is kept — the
                # vocabulary contract is unchanged, its value degenerated).
                None,
                result,
                member_id=self._identity.id,
                name=self._identity.name,
            )
        )

        collector: list[ActionRequest] = []
        builtins = make_member_builtins(collector=collector)
        # The rule's :fuel caps the completion actions' evaluation; the
        # cognitive gate and completion are temporally separated, so each
        # is capped at the same declared budget. Every form gets its own
        # budget, but all share one env dict (ADR 0021): a defn in one
        # form is visible to the forms after it — hence a bare Evaluator
        # per form, not eval_form (whose entry copies the env).
        action_fuel = self._rule.fuel if self._rule.fuel is not None else DEFAULT_FUEL
        for form in forms:
            try:
                Evaluator(builtins, action_fuel, DEFAULT_MAX_DEPTH).eval(form, env, 0)
            except FuelExhaustedError as e:
                self._logger.error(
                    "on_complete_fuel_exhausted",
                    session_id=session.id,
                    budget=e.budget,
                    error=str(e),
                )
            except EvalError as e:
                self._logger.error("on_complete_eval_failed", session_id=session.id, error=str(e))
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
                # ADR 0028: no trigger mail — the executor's session_mail
                # argument is always None on this path (emit without
                # explicit recipients degenerates to no recipients).
                await self._execute_action(request, None)
            except Exception as e:
                self._logger.error(
                    "on_complete_action_failed",
                    session_id=session.id,
                    action=request.kind,
                    error=str(e),
                    exc_info=True,
                )
