"""Message Router for Tlon.

The MessageRouter is a stateless component responsible for:
1. Receiving messages from EventBus (MESSAGE_NEW)
2. Persisting messages to MessageStore
3. Making routing decisions (who should receive this message)
4. Publishing routed messages (MESSAGE_ROUTED)

This separation allows:
- Router to be stateless and scalable
- Storage to be handled by dedicated MessageStore
- Routing logic to be independent of storage

Architecture:
    MessageRouter is a Device that depends on:
    - EventBus (for event subscription/publishing)
    - MessageStore (for persistence)
    - AgentInfoProvider (for routing decisions)

    It subscribes to MESSAGE_NEW events and publishes MESSAGE_ROUTED events.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from tlon.base.component import Component
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.base.tracing.trace_context import SpanCtx, continue_trace, start_span
from tlon.organization.messaging.message import Message
from tlon.organization.messaging.store import MessageStore

if TYPE_CHECKING:
    from tlon.base.config import AgentInfoProvider

logger = get_logger(__name__)


@dataclass
class RoutedMessage:
    """A message that has been routed to specific targets.

    Attributes:
        original: The original message
        target_ids: List of target recipient IDs (agents, clients, etc.)
        routing_reason: Why this routing decision was made
        mention_target_ids: Subset of target_ids that came from explicit
            @mention resolution (as opposed to proactive broadcast or
            client display). Used by the registry for wake-on-mention:
            only mention targets may wake a sleeping agent.
    """

    original: Message
    target_ids: list[str]
    routing_reason: str = ""
    mention_target_ids: list[str] = field(default_factory=list)


class MessageRouter(Component, Device):
    """Stateless message router for distributing messages to recipients.

    The router:
    - Subscribes to MESSAGE_NEW events
    - Stores messages via MessageStore
    - Decides who should receive the message
    - Publishes MESSAGE_ROUTED events

    Dependencies (constructor injected):
        - event_bus: EventBus for event subscription/publishing
        - message_store: MessageStore for persistence
        - agent_info_provider: AgentInfoProvider for routing decisions

    Example:
        >>> router = MessageRouter(
        ...     event_bus=event_bus,
        ...     message_store=message_store,
        ...     agent_info_provider=config_manager,
        ... )
        >>> await router.initialize()  # Begins listening for messages
        >>>
        >>> # Messages are automatically routed when published to EventBus
        >>> await event_bus.publish(Event(
        ...     type=EventType.MESSAGE_NEW,
        ...     payload=message,
        ...     source="human"
        ... ))
    """

    def __init__(
        self,
        event_bus: EventBus,
        message_store: MessageStore,
        agent_info_provider: Optional["AgentInfoProvider"] = None,
    ):
        """Initialize the message router.

        Args:
            event_bus: Event bus for subscribing and publishing
            message_store: Store for persisting messages
            agent_info_provider: Optional provider for agent configuration queries
        """
        # Initialize both parents
        Component.__init__(self, name="MessageRouter")
        Device.__init__(self, event_bus=event_bus, name="MessageRouter")
        self._message_store = message_store
        self._agent_info = agent_info_provider

        # Routing rules cache
        self._proactive_agents: set[str] = set()

        logger.info("message_router_constructed")

    async def initialize(self) -> None:
        """Start the router by subscribing to MESSAGE_NEW events."""
        self.subscribe(EventType.MESSAGE_NEW, self._on_message_new)
        logger.info("message_router_initialized")
        self._initialized = True

    async def shutdown(self) -> None:
        """Stop the router."""
        self.unsubscribe_all()
        self._initialized = False
        logger.info("message_router_shutdown")

    async def _on_message_new(self, event: Event) -> None:
        """Handle new message events.

        This is the core routing logic:
        1. Store the message
        2. Decide targets
        3. Publish routed message

        Args:
            event: The MESSAGE_NEW event
        """
        message: Message = event.payload
        attributes = {"message_id": message.id, "sender": message.sender_id}

        if event.trace_id:
            with continue_trace(event.trace_id, event.span_id), start_span(
                "message.routing", source="message_router", attributes=attributes
            ) as ctx:
                await self._route_message(message, ctx)
        else:
            # No carrier on the event: start a detached span
            with start_span(
                "message.routing", source="message_router", attributes=attributes
            ) as ctx:
                await self._route_message(message, ctx)

    async def _route_message(self, message: Message, ctx: SpanCtx) -> None:
        """Store, route, and publish a message within a tracing span.

        Args:
            message: The message to route
            ctx: Trace context of the routing span
        """
        try:
            # Step 1: Persist message (non-blocking, fire-and-forget is ok)
            await self._store_message(message)

            # Step 2: Decide routing targets
            targets, mention_targets = self._decide_targets(message)

            # Step 3: Publish routed message with trace context
            if targets:
                await self._publish_routed(message, targets, ctx, mention_targets)
                logger.debug("message_routed", message_id=message.id, targets=targets)
            else:
                logger.debug("message_unrouted", message_id=message.id)

        except Exception as e:
            logger.error(
                "message_routing_failed",
                message_id=message.id,
                error=str(e),
                exc_info=True,
            )

    async def _store_message(self, message: Message) -> None:
        """Store message to persistent store.

        Args:
            message: The message to store
        """
        try:
            await self._message_store.append(message)
        except Exception as e:
            logger.error("message_storage_failed", message_id=message.id, error=str(e))
            # Don't fail routing if storage fails - message flow is more important

    def _decide_targets(self, message: Message) -> tuple[list[str], list[str]]:
        """Decide which recipients should receive this message.

        Routing logic:
        - Human messages:
          - If has mentions: route to mentioned agents
          - If no mentions: route to all proactive agents
        - Agent messages:
          - Route to all clients (for display)
          - Route to agents mentioned in the message
        - System messages:
          - Route to all clients

        Args:
            message: The message to route

        Returns:
            Tuple of (all target recipient IDs, mention-resolved target IDs).
            The second element is the subset that came from explicit
            @mention resolution; proactive broadcast targets are excluded
            so the registry only wakes sleeping agents on real mentions.
        """
        targets: set[str] = set()
        mention_targets: set[str] = set()

        if message.sender_type == "human":
            t, m = self._route_human_message(message)
        elif message.sender_type == "agent":
            t, m = self._route_agent_message(message)
        elif message.sender_type == "system":
            t, m = self._route_system_message(message), set()
        else:
            t, m = set(), set()

        targets.update(t)
        mention_targets.update(m)
        return list(targets), list(mention_targets)

    def _route_human_message(self, message: Message) -> tuple[set[str], set[str]]:
        """Route a message from a human user.

        Args:
            message: The human message

        Returns:
            Tuple of (target IDs, mention-resolved target IDs)
        """
        targets: set[str] = set()
        mention_targets: set[str] = set()

        if message.mentions:
            # Explicit mentions - resolve to agent IDs and route
            resolved = self._resolve_mentions_to_ids(message.mentions)
            targets.update(resolved)
            mention_targets.update(resolved)
            logger.debug(
                "mentioned_agents_resolved",
                mentions=message.mentions,
                resolved=sorted(resolved),
            )
        else:
            # No mentions - route to proactive agents
            proactive = self._get_proactive_agents()
            targets.update(proactive)
            logger.debug("proactive_agents_selected", agents=sorted(proactive))

        return targets, mention_targets

    def _route_agent_message(self, message: Message) -> tuple[set[str], set[str]]:
        """Route a message from an agent.

        Args:
            message: The agent message

        Returns:
            Tuple of (target IDs incl. clients, mention-resolved target IDs)
        """
        targets: set[str] = set()
        mention_targets: set[str] = set()

        # Agent messages go to clients for display
        targets.add("@clients")

        # Also route to agents that are mentioned (resolve names to IDs)
        if message.mentions:
            resolved = self._resolve_mentions_to_ids(message.mentions)
            targets.update(resolved)
            mention_targets.update(resolved)
            logger.debug(
                "agent_message_targets_resolved", resolved_mentions=sorted(resolved)
            )

        return targets, mention_targets

    def _route_system_message(self, message: Message) -> set[str]:
        """Route a system message.

        System messages go to all clients for display.

        Args:
            message: The system message

        Returns:
            Set of target IDs
        """
        return {"@clients"}

    def _get_proactive_agents(self) -> set[str]:
        """Get the set of proactive agent IDs.

        Proactive agents respond to general human messages (no @mentions).

        Returns:
            Set of agent IDs that are proactive
        """
        # Refresh from provider if available
        if self._agent_info:
            self._proactive_agents = {
                agent_id
                for agent_id in self._agent_info.get_all_ids()
                if self._agent_info.is_proactive(agent_id)
            }

        return self._proactive_agents.copy()

    def _is_proactive(self, agent_id: str) -> bool:
        """Check if an agent is proactive.

        Args:
            agent_id: The agent ID to check

        Returns:
            True if the agent responds to general messages
        """
        if not self._agent_info:
            return False

        return self._agent_info.is_proactive(agent_id)

    def _resolve_mentions_to_ids(self, mentions: list[str]) -> set[str]:
        """Resolve mentions (names or IDs) to agent IDs.

        Mentions can be either agent IDs or agent names.
        This method converts all mentions to agent IDs for routing.

        Args:
            mentions: List of mentions (names or IDs)

        Returns:
            Set of resolved agent IDs
        """
        if not self._agent_info:
            # No provider, assume mentions are already IDs
            return set(mentions)

        resolved: set[str] = set()
        all_ids = self._agent_info.get_all_ids()

        for mention in mentions:
            # Check if it's already an ID
            if mention in all_ids:
                resolved.add(mention)
                continue

            # Check if it's a name - look up by name
            found = False
            for agent_id in all_ids:
                config = self._agent_info.get_config(agent_id)
                if config and config.name == mention:
                    resolved.add(agent_id)
                    found = True
                    break

            if not found:
                # Could not resolve, log warning but keep original
                # (might be a future agent or typo)
                logger.warning("mention_resolution_failed", mention=mention)
                resolved.add(mention)  # Keep as-is, agent will ignore if not matching

        return resolved

    async def _publish_routed(
        self,
        message: Message,
        targets: list[str],
        ctx: SpanCtx | None = None,
        mention_target_ids: list[str] | None = None,
    ) -> None:
        """Publish a routed message event.

        Args:
            message: The original message
            targets: List of target recipient IDs
            ctx: Optional trace context
            mention_target_ids: Subset of targets resolved from @mentions
        """
        routed = RoutedMessage(
            original=message,
            target_ids=targets,
            routing_reason=self._get_routing_reason(message, targets),
            mention_target_ids=mention_target_ids or [],
        )

        # Build event with trace context
        event_kwargs = {
            "type": EventType.MESSAGE_ROUTED,
            "payload": routed,
            "source": "message_router",
        }
        if ctx and ctx.trace_id:
            event_kwargs["trace_id"] = ctx.trace_id
            event_kwargs["span_id"] = ctx.span_id
            event_kwargs["parent_span_id"] = ctx.parent_span_id

        await self.publish(Event(**event_kwargs))

    def _get_routing_reason(self, message: Message, targets: list[str]) -> str:
        """Generate a human-readable routing reason for logging.

        Args:
            message: The message
            targets: The routing targets

        Returns:
            Description of why this routing occurred
        """
        if message.sender_type == "human":
            if message.mentions:
                return f"explicit_mentions: {message.mentions}"
            else:
                return "proactive_broadcast"
        elif message.sender_type == "agent":
            return f"agent_response_to: {targets}"
        else:
            return "system_broadcast"

    # ==================== Public API for manual routing ====================

    async def route_manually(self, message: Message, targets: list[str]) -> None:
        """Manually route a message to specific targets.

        This bypasses the automatic routing logic.

        Args:
            message: The message to route
            targets: Explicit target IDs
        """
        # Store first
        await self._store_message(message)

        # Then publish routed event
        await self._publish_routed(message, targets)

    def update_proactive_agents(self, agent_ids: set[str]) -> None:
        """Update the set of proactive agents.

        This can be called when agent configurations change.

        Args:
            agent_ids: New set of proactive agent IDs
        """
        self._proactive_agents = agent_ids.copy()
        logger.info("proactive_agents_updated", agent_ids=sorted(agent_ids))
