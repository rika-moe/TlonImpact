"""Trigger evaluation for Agent.

Decides whether the agent should respond to an incoming routed message
(mention-based or proactive), extracted from the Agent god class.
"""

from typing import TYPE_CHECKING

from tlon.base.logger import get_logger
from tlon.organization.messaging.message import Message

if TYPE_CHECKING:
    from tlon.agents.agent import Agent

logger = get_logger(__name__)


class TriggerEvaluator:
    """Evaluates whether an incoming message should trigger a response.

    Holds a back-reference to the owning Agent for identity (id/name)
    and configuration (proactive flag).
    """

    def __init__(self, agent: "Agent"):
        self._agent = agent

    def should_respond(self, message: Message) -> bool:
        """Determine if we should respond to this message."""
        if message.sender_id == self._agent.id:
            return False

        if self.is_mentioned(message):
            logger.debug("agent_will_respond", reason="mentioned", sender=message.sender_name)
            return True

        if (
            self._agent.config.proactive
            and message.sender_type == "human"
            and not message.mentions
        ):
            logger.info("agent_will_respond", reason="proactive", sender=message.sender_name)
            return True

        logger.debug("agent_will_not_respond", sender=message.sender_name)
        return False

    def is_mentioned(self, message: Message) -> bool:
        """Check if this agent is mentioned in the message."""
        if self._agent.id in message.mentions:
            return True
        return self._agent.name in message.mentions
