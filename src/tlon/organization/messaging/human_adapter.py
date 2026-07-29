"""Human Adapter: human users as bus citizens of Congress.

A HumanAdapter is the on-bus presence of a human user (one per username);
client sessions are its attachments. It carries identity, lifecycle and the
ability to speak (publish MAIL_NEW) — nothing of the LLM / memory /
Docker machinery of a ReactAgent.

R1b bridge (docs/design/mail.md): the WS protocol still carries only
content+mentions, so ``speak`` keeps the legacy ``@名字`` parsing and
resolves those names into recipient addresses. A mail with zero
resolvable recipients is rejected (proactive is dead — an unaddressed
mail is illegal). The @ bridge itself is replaced by explicit recipient
fields in R2.

Note (v1): HumanAdapter does NOT subscribe to MAIL_DELIVERED. The
NotificationService already broadcasts all events to all sessions, so
adapter-side forwarding would double-deliver. Per-recipient filtering is
deferred to R2.

See docs/design/members.md (阶段 B).
"""

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from tlon.base.component import Component
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.base.tracing.trace_context import start_span
from tlon.organization.mail.mail import Mail, address_for_member, is_mail_address
from tlon.organization.messaging.member import Member

if TYPE_CHECKING:
    from tlon.base.config import AgentInfoProvider

logger = get_logger(__name__)

# Default username when the client provides none.
DEFAULT_USERNAME = "guest"

# Prefix of the internal human member-id namespace ("human:<username>").
HUMAN_ID_PREFIX = "human:"

# Matches "@名字" — the run of non-whitespace characters after '@'.
_MENTION_PATTERN = re.compile(r"@(\S+)")

# Resolves a mention (name, id or full address) to a mail address.
AddressResolver = Callable[[str], str | None]


def parse_mentions(content: str) -> list[str]:
    """Extract raw @mention names from message content (R1b bridge).

    Args:
        content: Message content

    Returns:
        List of mentioned names (order of appearance, duplicates kept)
    """
    return _MENTION_PATTERN.findall(content)


class HumanAdapter(Member):
    """Member representing one human user; sessions are its attachments.

    An adapter is born on the first session attach and sleeps on the last
    detach (managed by AdapterManager). Multiple sessions of the same
    username share one adapter (refcount semantics).
    """

    def __init__(
        self, username: str, event_bus: EventBus, address_resolver: AddressResolver
    ):
        """Initialize the human adapter.

        Args:
            username: Human username (also the display name)
            event_bus: Event bus for publishing mail
            address_resolver: Mention name -> mail address resolution
                (injected by the AdapterManager)
        """
        Component.__init__(self, name=f"HumanAdapter:{username}")
        Device.__init__(self, event_bus=event_bus, name=f"HumanAdapter:{username}")
        self._username = username
        self._address_resolver = address_resolver
        self._session_ids: set[str] = set()

    @property
    def username(self) -> str:
        """The human username (display name)."""
        return self._username

    @property
    def member_id(self) -> str:
        """Member identity on the bus ("human:<username>")."""
        return f"{HUMAN_ID_PREFIX}{self._username}"

    @property
    def member_type(self) -> str:
        """Member type; humans are adapter members."""
        return "adapter"

    @property
    def address(self) -> str:
        """The human's mail address ("<username>@tlon.internal")."""
        return address_for_member(self.member_id)

    @property
    def session_count(self) -> int:
        """Number of currently attached sessions."""
        return len(self._session_ids)

    def get_state(self) -> str:
        """Get adapter state: "online" with sessions, "offline" without."""
        return "online" if self._session_ids else "offline"

    def attach_session(self, session_id: str) -> None:
        """Attach a client session (refcount +1).

        Args:
            session_id: Session to attach
        """
        self._session_ids.add(session_id)

    def detach_session(self, session_id: str) -> None:
        """Detach a client session (refcount -1).

        Args:
            session_id: Session to detach
        """
        self._session_ids.discard(session_id)

    async def initialize(self) -> None:
        """Initialize the adapter (v1: no bus subscriptions)."""
        self._initialized = True
        logger.info("human_adapter_initialized", username=self._username)

    async def shutdown(self) -> None:
        """Shutdown the adapter and release bus subscriptions."""
        self.unsubscribe_all()
        self._initialized = False
        logger.info("human_adapter_shutdown", username=self._username)

    async def speak(self, content: str, mentions: list[str] | None = None) -> Mail:
        """Speak into Congress: publish the human's mail as MAIL_NEW.

        R1b bridge: @mentions inside the content are extracted, merged
        with the explicit mentions and resolved to recipient addresses.
        Unresolvable mentions are dropped with a warning; a mail with zero
        resolvable recipients is rejected outright — with proactive gone,
        an unaddressed mail is illegal.

        Args:
            content: Message content
            mentions: Optional explicit mentions (raw names, ids or
                full addresses)

        Returns:
            The created mail

        Raises:
            ValueError: If no mention resolves to a recipient address
        """
        raw = list(dict.fromkeys([*parse_mentions(content), *(mentions or [])]))
        to: list[str] = []
        for name in raw:
            address = self._address_resolver(name)
            if address is None:
                logger.warning("mention_unresolved", username=self._username, mention=name)
                continue
            if address not in to:
                to.append(address)

        if not to:
            raise ValueError(
                "没有可送达的收件人：请用 @名字 指定至少一个收件人"
            )

        mail = Mail.create(
            from_address=self.address,
            to=to,
            content=content,
        )

        with start_span(
            "mail.received",
            source="human_adapter",
            attributes={"mail_id": mail.id, "sender": self.member_id},
        ) as ctx:
            await self.publish(
                Event(
                    type=EventType.MAIL_NEW,
                    payload=mail,
                    source=self.member_id,
                    trace_id=ctx.trace_id,
                    span_id=ctx.span_id,
                    parent_span_id=ctx.parent_span_id,
                )
            )

        logger.info(
            "human_mail_published",
            username=self._username,
            mail_id=mail.id,
            to=mail.to,
        )
        return mail


class AdapterManager(Component):
    """Manages HumanAdapter lifecycle (username → adapter, refcounted).

    Not registered in the ComponentRegistry: adapters have a dynamic
    lifecycle (attach/detach driven), the manager itself is a regular
    ServerApp component with initialize/shutdown.
    """

    def __init__(self, event_bus: EventBus, agent_info_provider: "AgentInfoProvider"):
        """Initialize the adapter manager.

        Args:
            event_bus: Event bus adapters publish on
            agent_info_provider: Provider for agent config queries
                (mention → address resolution for the R1b @ bridge)
        """
        super().__init__(name="AdapterManager")
        self._event_bus = event_bus
        self._agent_info = agent_info_provider
        self._adapters: dict[str, HumanAdapter] = {}  # username -> adapter
        self._sessions: dict[str, str] = {}  # session_id -> username

    async def initialize(self) -> None:
        """Initialize the manager (adapters are created lazily on attach)."""
        self._initialized = True
        logger.info("adapter_manager_initialized")

    async def shutdown(self) -> None:
        """Shutdown all live adapters and clear state."""
        for adapter in list(self._adapters.values()):
            await adapter.shutdown()
        self._adapters.clear()
        self._sessions.clear()
        self._initialized = False
        logger.info("adapter_manager_shutdown")

    async def attach(self, username: str, session_id: str) -> HumanAdapter:
        """Attach a session to the user's adapter, creating it if needed.

        Args:
            username: Human username
            session_id: Session to attach

        Returns:
            The user's adapter (shared across same-name sessions)
        """
        adapter = self._adapters.get(username)
        if adapter is None:
            adapter = HumanAdapter(username, self._event_bus, self.resolve_address)
            await adapter.initialize()
            self._adapters[username] = adapter
            logger.info(
                "human_adapter_created", username=username, member_id=adapter.member_id
            )

        adapter.attach_session(session_id)
        self._sessions[session_id] = username
        logger.info(
            "human_session_attached",
            username=username,
            session_id=session_id,
            session_count=adapter.session_count,
        )
        return adapter

    async def detach(self, session_id: str) -> None:
        """Detach a session; shutdown the adapter when its last session leaves.

        Args:
            session_id: Session to detach
        """
        username = self._sessions.pop(session_id, None)
        if username is None:
            return

        adapter = self._adapters.get(username)
        if adapter is None:
            return

        adapter.detach_session(session_id)
        logger.info(
            "human_session_detached",
            username=username,
            session_id=session_id,
            session_count=adapter.session_count,
        )

        if adapter.session_count == 0:
            await adapter.shutdown()
            del self._adapters[username]
            logger.info("human_adapter_removed", username=username)

    async def publish(
        self, session_id: str, content: str, mentions: list[str] | None = None
    ) -> Mail | None:
        """Publish a mail from the user owning this session.

        Args:
            session_id: Sender's session ID
            content: Message content
            mentions: Optional explicit mentions

        Returns:
            The created mail, or None if no adapter is attached to
            this session (rejected, logged as warning)

        Raises:
            ValueError: If no mention resolves to a recipient address
        """
        username = self._sessions.get(session_id)
        adapter = self._adapters.get(username) if username else None
        if adapter is None:
            logger.warning("publish_rejected_no_adapter", session_id=session_id)
            return None
        return await adapter.speak(content, mentions)

    def get_adapter(self, username: str) -> HumanAdapter | None:
        """Get a user's adapter.

        Args:
            username: Human username

        Returns:
            The adapter, or None if the user is offline
        """
        return self._adapters.get(username)

    def list_online(self) -> list[HumanAdapter]:
        """List all online human adapters (sorted by username).

        Returns:
            Online adapters
        """
        return [self._adapters[name] for name in sorted(self._adapters)]

    def resolve_address(self, mention: str) -> str | None:
        """Resolve a mention to a mail address (R1b @ bridge).

        Accepts a full mail address (passed through), an agent id, an
        agent display name, an online human's username, or the
        "human:<username>" form.

        Args:
            mention: Mention to resolve

        Returns:
            The member's mail address, or None if unresolvable
        """
        name = mention.lstrip("@")
        if is_mail_address(name):
            return name

        # Agent id direct hit, then agent display name lookup
        if self._agent_info.get_config(name) is not None:
            return address_for_member(name)
        for agent_id in self._agent_info.get_all_ids():
            config = self._agent_info.get_config(agent_id)
            if config and config.name == name:
                return address_for_member(agent_id)

        # Online human ("alice" or "human:alice")
        username = (
            name[len(HUMAN_ID_PREFIX):] if name.startswith(HUMAN_ID_PREFIX) else name
        )
        adapter = self._adapters.get(username)
        if adapter is not None:
            return adapter.address

        return None

    def username_for(self, session_id: str) -> str | None:
        """Get the username owning a session.

        Args:
            session_id: Session ID

        Returns:
            The username, or None if the session is not attached
        """
        return self._sessions.get(session_id)
