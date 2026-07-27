"""Human Adapter: human users as bus citizens of Congress.

A HumanAdapter is the on-bus presence of a human user (one per username);
client sessions are its attachments. It carries identity, lifecycle and the
ability to speak (publish MESSAGE_NEW) — nothing of the LLM / memory /
Docker machinery of a ReactAgent.

Note (v1): HumanAdapter does NOT subscribe to MESSAGE_ROUTED. The
NotificationService already broadcasts all events to all sessions (single
room, fully public), so adapter-side forwarding would double-deliver.
Targeted delivery is deferred to the private-chat / multi-room rework.

See docs/design/members.md (阶段 B).
"""

import re

from tlon.base.component import Component
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.base.tracing.trace_context import start_span
from tlon.organization.messaging.member import Member
from tlon.organization.messaging.message import Message

logger = get_logger(__name__)

# Default username when the client provides none.
DEFAULT_USERNAME = "guest"

# Prefix of the internal human member-id namespace ("human:<username>").
HUMAN_ID_PREFIX = "human:"

# Matches "@名字" — the run of non-whitespace characters after '@'.
_MENTION_PATTERN = re.compile(r"@(\S+)")


def parse_mentions(content: str) -> list[str]:
    """Extract raw @mention names from message content.

    The adapter does NOT resolve names to member IDs — that is the job of
    the router / MemberDirectory. Raw names are kept as-is.

    Args:
        content: Message content

    Returns:
        List of mentioned names (order of appearance, duplicates kept)
    """
    return _MENTION_PATTERN.findall(content)


def build_human_message(
    username: str,
    content: str,
    mentions: list[str] | None = None,
) -> Message:
    """Build a human message with the standard adapter identity format.

    Mentions parsed from the content are merged with explicit ones, keeping
    raw names — name→ID resolution happens downstream in the router.

    Args:
        username: Human username (identity source)
        content: Message content
        mentions: Optional explicit mentions (raw names or IDs)

    Returns:
        The created message
    """
    merged = list(dict.fromkeys([*parse_mentions(content), *(mentions or [])]))
    return Message.create(
        sender_id=f"{HUMAN_ID_PREFIX}{username}",
        sender_name=username,
        sender_type="human",
        content=content,
        mentions=merged,
    )


class HumanAdapter(Member):
    """Member representing one human user; sessions are its attachments.

    An adapter is born on the first session attach and sleeps on the last
    detach (managed by AdapterManager). Multiple sessions of the same
    username share one adapter (refcount semantics).
    """

    def __init__(self, username: str, event_bus: EventBus):
        """Initialize the human adapter.

        Args:
            username: Human username (also the display name)
            event_bus: Event bus for publishing messages
        """
        Component.__init__(self, name=f"HumanAdapter:{username}")
        Device.__init__(self, event_bus=event_bus, name=f"HumanAdapter:{username}")
        self._username = username
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
    def triggers_proactive(self) -> bool:
        """Human undirected messages invite proactive agent responses."""
        return True

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

    async def speak(self, content: str, mentions: list[str] | None = None) -> Message:
        """Speak into Congress: publish the human's message as MESSAGE_NEW.

        @mentions inside the content are extracted and merged with the
        explicit mentions; name→ID resolution is left to the router.

        Args:
            content: Message content
            mentions: Optional explicit mentions (raw names or IDs)

        Returns:
            The created message
        """
        message = build_human_message(self._username, content, mentions)

        with start_span(
            "message.received",
            source="human_adapter",
            attributes={"message_id": message.id, "sender": self.member_id},
        ) as ctx:
            await self.publish(
                Event(
                    type=EventType.MESSAGE_NEW,
                    payload=message,
                    source=self.member_id,
                    trace_id=ctx.trace_id,
                    span_id=ctx.span_id,
                    parent_span_id=ctx.parent_span_id,
                )
            )

        logger.info(
            "human_message_published",
            username=self._username,
            message_id=message.id,
            mentions=message.mentions,
        )
        return message


class AdapterManager(Component):
    """Manages HumanAdapter lifecycle (username → adapter, refcounted).

    Not registered in the ComponentRegistry: adapters have a dynamic
    lifecycle (attach/detach driven), the manager itself is a regular
    ServerApp component with initialize/shutdown.
    """

    def __init__(self, event_bus: EventBus):
        """Initialize the adapter manager.

        Args:
            event_bus: Event bus adapters publish on
        """
        super().__init__(name="AdapterManager")
        self._event_bus = event_bus
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
            adapter = HumanAdapter(username, self._event_bus)
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
    ) -> Message | None:
        """Publish a message from the user owning this session.

        Args:
            session_id: Sender's session ID
            content: Message content
            mentions: Optional explicit mentions

        Returns:
            The created message, or None if no adapter is attached to
            this session (rejected, logged as warning)
        """
        username = self._sessions.get(session_id)
        adapter = self._adapters.get(username) if username else None
        if adapter is None:
            logger.warning("publish_rejected_no_adapter", session_id=session_id)
            return None
        return await adapter.speak(content, mentions)

    async def publish_as(
        self, username: str, content: str, mentions: list[str] | None = None
    ) -> Message:
        """Publish a message as a user, with or without an attached session.

        Pragmatic path for attachment-less callers (e.g. a REST endpoint):
        if the user's adapter exists it speaks through it; otherwise the
        message is published with the same identity format without
        creating an adapter (no presence is claimed).

        Args:
            username: Human username to speak as
            content: Message content
            mentions: Optional explicit mentions

        Returns:
            The created message
        """
        adapter = self._adapters.get(username)
        if adapter is not None:
            return await adapter.speak(content, mentions)

        # No adapter: publish with the same identity format without
        # creating one (no presence is claimed)
        message = build_human_message(username, content, mentions)
        member_id = message.sender_id
        with start_span(
            "message.received",
            source="human_adapter",
            attributes={"message_id": message.id, "sender": member_id},
        ) as ctx:
            await self._event_bus.publish(
                Event(
                    type=EventType.MESSAGE_NEW,
                    payload=message,
                    source=member_id,
                    trace_id=ctx.trace_id,
                    span_id=ctx.span_id,
                    parent_span_id=ctx.parent_span_id,
                )
            )
        return message

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

    def resolve_human(self, name_or_id: str) -> str | None:
        """Resolve a human mention to a member ID (online humans only).

        Accepts both "alice" and "human:alice" forms.

        Args:
            name_or_id: Username or human member ID

        Returns:
            The member ID ("human:<username>"), or None if offline/unknown
        """
        username = (
            name_or_id[len(HUMAN_ID_PREFIX) :]
            if name_or_id.startswith(HUMAN_ID_PREFIX)
            else name_or_id
        )
        adapter = self._adapters.get(username)
        return adapter.member_id if adapter else None

    def is_human_member(self, sender_id: str) -> bool:
        """Check whether a sender ID belongs to an online human adapter.

        Args:
            sender_id: Sender ID to check

        Returns:
            True if the sender is a known online human member
        """
        if not sender_id.startswith(HUMAN_ID_PREFIX):
            return False
        return sender_id[len(HUMAN_ID_PREFIX) :] in self._adapters

    def username_for(self, session_id: str) -> str | None:
        """Get the username owning a session.

        Args:
            session_id: Session ID

        Returns:
            The username, or None if the session is not attached
        """
        return self._sessions.get(session_id)
