"""Trigger evaluation for Agent.

Decides whether the agent should respond to an incoming routed message.
Two modes per ``TriggerSpec``:

- expression mode (``trigger.when`` set): the L1 expression is evaluated
  by the restricted evaluator (``agents/eval.py``) with ``msg``/``self``
  in scope; any evaluation failure safely defaults to not responding;
- data mode (``when`` is None): declarative mention/proactive rules.
"""

from typing import TYPE_CHECKING

from tlon.agents.edn import Form
from tlon.agents.eval import TRIGGER_BUILTINS, EvalError, eval_form
from tlon.base.logger import get_logger
from tlon.organization.messaging.message import Message

if TYPE_CHECKING:
    from tlon.agents.agent import Agent

logger = get_logger(__name__)


class TriggerEvaluator:
    """Evaluates whether an incoming message should trigger a response.

    Holds a back-reference to the owning Agent for identity (id/name)
    and the behavior spec (trigger rules).
    """

    def __init__(self, agent: "Agent"):
        self._agent = agent

    def should_respond(self, message: Message) -> bool:
        """Determine if we should respond to this message."""
        if message.sender_id == self._agent.id:
            return False

        trigger = self._agent.spec.trigger

        if trigger.when is not None:
            return self._evaluate_expression(trigger.when, message)

        if trigger.on_mention and self.is_mentioned(message):
            logger.debug("agent_will_respond", reason="mentioned", sender=message.sender_name)
            return True

        if trigger.proactive and message.sender_type != "agent" and not message.mentions:
            logger.info("agent_will_respond", reason="proactive", sender=message.sender_name)
            return True

        logger.debug("agent_will_not_respond", sender=message.sender_name)
        return False

    def _evaluate_expression(self, form: Form, message: Message) -> bool:
        """Evaluate the trigger expression; failures default to no response."""
        env = {
            "msg": message,
            "self": {"id": self._agent.id, "name": self._agent.name},
        }
        try:
            result = eval_form(form, env, TRIGGER_BUILTINS)
        except EvalError as e:
            logger.warning("trigger_eval_failed", error=str(e), sender=message.sender_name)
            return False
        hit = bool(result)
        logger.debug("trigger_expr_result", result=hit, sender=message.sender_name)
        return hit

    def is_mentioned(self, message: Message) -> bool:
        """Check if this agent is mentioned in the message."""
        if self._agent.id in message.mentions:
            return True
        return self._agent.name in message.mentions
