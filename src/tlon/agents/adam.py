"""Adam — the built-in init process of the agent world (PID 1).

Policy/mechanism split (docs/design/members.md, 阶段 D): the AgentRegistry
is pure mechanism (assembly, run table, locks, budget, idle-fact
detection); Adam is the policy. All three lifecycle policy lines live here:

1. boot — after initialize(), every registered agent with
   ``activation != "lazy"`` that is not running gets an
   AGENT_START_REQUEST{reason="boot"}.
2. wake (socket activation) — MESSAGE_ROUTED whose mention targets include
   a sleeping agent gets AGENT_START_REQUEST{reason="wake", trigger_event}
   carrying the original event for redelivery.
3. drain — the registry's AGENT_IDLE fact event is turned into an
   AGENT_STOP_REQUEST{reason="drain"}.

Adam is a built-in Member (``member_id="adam"``, ``member_type="script"``):
it does not come from any user spec file, is not registered in the agent
registry, and does not appear in MemberDirectory. It holds no run-state of
its own — it queries the registry's read-only interfaces
(``get_agent`` / ``get_config`` / ``get_all``) instead of reconstructing
state from events. Lifecycle decisions carry no dedup state either: the
registry executes requests idempotently.

Receptionist duty: when Adam decides to wake a sleeping agent, it also
posts a system message ("正在唤醒 X…") so the human is not facing silence
while assembly runs; a wake failure posts a failure notice. The small
``_wake_announced`` set dedups these UX announcements only (it never
gates lifecycle decisions) and is cleared on AGENT_WOKEN /
AGENT_WAKE_FAILED. System messages are skipped by the LLM context
builder, so agents never see them.

The dependency is one-directional: Adam holds a registry reference; the
registry never imports Adam (kernel first, PID 1 second — the DAG stays
acyclic).
"""

from typing import TYPE_CHECKING

from tlon.agents.lifecycle import StartRequest, StopRequest
from tlon.base.component import Component
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.organization.messaging.member import Member
from tlon.organization.messaging.message import Message
from tlon.organization.messaging.router import RoutedMessage

if TYPE_CHECKING:
    from tlon.agents.agent_registry import AgentRegistry

logger = get_logger(__name__)


class Adam(Member):
    """Built-in lifecycle policy member: boot, wake-on-mention, idle drain.

    All work happens in the EventBus dispatch context (v1: no worker task;
    the bus already isolates handler exceptions). Adam's decisions are
    request events on the bus; execution — including all run-state
    re-validation — is the registry's job.
    """

    def __init__(self, event_bus: EventBus, agent_registry: "AgentRegistry"):
        """Initialize Adam.

        Args:
            event_bus: Event bus for subscriptions and request publishing
            agent_registry: The registry to query (read-only interfaces)
                and to address with start/stop requests
        """
        Component.__init__(self, name="Adam")
        Device.__init__(self, event_bus=event_bus, name="Adam")
        self._registry = agent_registry
        # UX announcement dedup (never gates lifecycle decisions)
        self._wake_announced: set[str] = set()

    @property
    def member_id(self) -> str:
        """Member identity on the bus ("adam")."""
        return "adam"

    @property
    def member_type(self) -> str:
        """Member type; Adam is a built-in script member."""
        return "script"

    @property
    def triggers_proactive(self) -> bool:
        """Adam speaks only system messages, which never trigger proactive."""
        return False

    def get_state(self) -> str:
        """Get member state: "active" once initialized, else "offline"."""
        return "active" if self._initialized else "offline"

    async def initialize(self) -> None:
        """Subscribe to the bus, then issue boot start requests.

        Boot is asynchronous by design: requests are published and
        initialize() returns without waiting for the agents to come up —
        the registry executes them in background tasks.
        """
        self.subscribe(EventType.MESSAGE_ROUTED, self._on_message_routed)
        self.subscribe(EventType.AGENT_IDLE, self._on_agent_idle)
        self.subscribe(EventType.AGENT_WOKEN, self._on_agent_woken)
        self.subscribe(EventType.AGENT_WAKE_FAILED, self._on_agent_wake_failed)
        self._initialized = True

        boot_ids: list[str] = []
        for agent_id in self._registry.get_all():
            config = self._registry.get_config(agent_id)
            if config is None or config.activation == "lazy":
                continue
            if self._registry.get_agent(agent_id) is not None:
                continue
            boot_ids.append(agent_id)
            await self._publish_start_request(agent_id, "boot", trigger_event=None)

        logger.info("adam_initialized", boot_requested=boot_ids)

    async def shutdown(self) -> None:
        """Shutdown Adam and release bus subscriptions."""
        self.unsubscribe_all()
        self._initialized = False
        logger.info("adam_shutdown")

    async def _on_message_routed(self, event: Event) -> None:
        """Request a wake for sleeping agents targeted by explicit @mentions.

        Only mention-resolved targets wake an agent — proactive broadcast
        targets never do, otherwise scale-to-zero would be defeated by
        every general message. Runs in the dispatch context and stays
        cheap: registry queries plus one publish per sleeping target.
        """
        if not isinstance(event.payload, RoutedMessage):
            return

        for agent_id in event.payload.mention_target_ids:
            if self._registry.get_agent(agent_id) is not None:
                continue
            config = self._registry.get_config(agent_id)
            if config is None:
                continue
            await self._publish_start_request(agent_id, "wake", trigger_event=event)
            if agent_id not in self._wake_announced:
                self._wake_announced.add(agent_id)
                await self._announce(f"正在唤醒 {config.name}…", event)

    async def _on_agent_idle(self, event: Event) -> None:
        """Turn the registry's idle fact into a drain decision."""
        if not isinstance(event.payload, dict):
            return
        agent_id = event.payload.get("agent_id")
        if not agent_id:
            return
        logger.info("adam_drain_requested", agent_id=agent_id, reason="drain")
        await self.publish(
            Event(
                type=EventType.AGENT_STOP_REQUEST,
                payload=StopRequest(agent_id=agent_id, reason="drain"),
                source=self.member_id,
            )
        )

    async def _on_agent_woken(self, event: Event) -> None:
        """Clear the announcement dedup once the agent is up."""
        if isinstance(event.payload, dict):
            self._wake_announced.discard(event.payload.get("agent_id", ""))

    async def _on_agent_wake_failed(self, event: Event) -> None:
        """Clear the dedup and post a failure notice."""
        if not isinstance(event.payload, dict):
            return
        agent_id = event.payload.get("agent_id", "")
        self._wake_announced.discard(agent_id)
        config = self._registry.get_config(agent_id)
        name = config.name if config else agent_id
        await self._announce(f"唤醒 {name} 失败，请稍后再试。", event)

    async def _announce(self, content: str, trigger_event: Event | None) -> None:
        """Post a system message into the room (receptionist duty).

        System messages are stored and broadcast to clients by the
        NotificationService, skipped by the LLM context builder, and
        never trigger proactive responses (Adam is not a human adapter).
        """
        message = Message.create(
            sender_id=self.member_id,
            sender_name="亚当",
            sender_type="system",
            content=content,
        )
        await self.publish(
            Event(
                type=EventType.MESSAGE_NEW,
                payload=message,
                source=self.member_id,
                trace_id=trigger_event.trace_id if trigger_event else None,
                span_id=trigger_event.span_id if trigger_event else None,
                parent_span_id=trigger_event.parent_span_id if trigger_event else None,
            )
        )

    async def _publish_start_request(
        self, agent_id: str, reason: str, trigger_event: Event | None
    ) -> None:
        """Publish one AGENT_START_REQUEST decision.

        For wake requests the event carries the trigger's trace carrier so
        the wake joins the routed message's trace tree; the registry takes
        the carrier from ``trigger_event`` for its own spans and events.

        Args:
            agent_id: Agent to start
            reason: "boot" or "wake"
            trigger_event: Original MESSAGE_ROUTED event (wake only)
        """
        logger.info(
            "adam_wake_requested" if reason == "wake" else "adam_boot_requested",
            agent_id=agent_id,
            reason=reason,
        )
        await self.publish(
            Event(
                type=EventType.AGENT_START_REQUEST,
                payload=StartRequest(
                    agent_id=agent_id, reason=reason, trigger_event=trigger_event
                ),
                source=self.member_id,
                trace_id=trigger_event.trace_id if trigger_event else None,
                span_id=trigger_event.span_id if trigger_event else None,
                parent_span_id=trigger_event.parent_span_id if trigger_event else None,
            )
        )
