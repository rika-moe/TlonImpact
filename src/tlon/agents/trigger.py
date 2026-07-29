"""Trigger evaluation for Agent.

Decides whether the agent should respond to an incoming mail. Two modes
per ``TriggerSpec``:

- expression mode (``trigger.when`` set): the L1 expression is evaluated
  by the restricted evaluator (``agents/eval.py``) with ``msg``/``self``
  in scope; any evaluation failure safely defaults to not responding;
- data mode (``when`` is None): the declarative rule — the agent's own
  address in To or CC means the mail is processed.
"""

from typing import TYPE_CHECKING

from tlon.agents.edn import Form
from tlon.agents.eval import TRIGGER_BUILTINS, EvalError, eval_form
from tlon.base.logger import get_logger
from tlon.organization.mail.mail import Mail

if TYPE_CHECKING:
    from tlon.agents.agent import Agent

logger = get_logger(__name__)


class TriggerEvaluator:
    """Evaluates whether an incoming mail should trigger a response.

    Holds a back-reference to the owning Agent for identity (address)
    and the behavior spec (trigger rules).
    """

    def __init__(self, agent: "Agent"):
        self._agent = agent

    def should_respond(self, mail: Mail) -> bool:
        """Determine if we should respond to this mail."""
        if mail.from_address == self._agent.address:
            return False

        trigger = self._agent.spec.trigger

        if trigger.when is not None:
            return self._evaluate_expression(trigger.when, mail)

        if trigger.on_mention and self.is_addressed(mail):
            logger.debug("agent_will_respond", reason="addressed", sender=mail.from_address)
            return True

        logger.debug("agent_will_not_respond", sender=mail.from_address)
        return False

    def _evaluate_expression(self, form: Form, mail: Mail) -> bool:
        """Evaluate the trigger expression; failures default to no response."""
        env = {
            "msg": mail,
            "self": {"id": self._agent.id, "name": self._agent.name},
        }
        try:
            result = eval_form(form, env, TRIGGER_BUILTINS)
        except EvalError as e:
            logger.warning("trigger_eval_failed", error=str(e), sender=mail.from_address)
            return False
        hit = bool(result)
        logger.debug("trigger_expr_result", result=hit, sender=mail.from_address)
        return hit

    def is_addressed(self, mail: Mail) -> bool:
        """Check if this agent's address is in the mail's To or CC."""
        address = self._agent.address
        return address in mail.to or address in mail.cc
