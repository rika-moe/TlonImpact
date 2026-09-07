"""Human Adapter: human users as bus citizens of Congress.

A HumanAdapter is the on-bus presence of a human user (one per username);
client sessions are its attachments. It carries identity, lifecycle and the
ability to speak (publish MAIL_NEW) — nothing of the LLM / memory /
Docker machinery of a cognitive member (MemberRuntime).

R2 protocol (docs/design/mail.md): ``speak`` accepts explicit ``to`` /
``cc`` / ``subject`` fields; explicit addressing is the only entry point
(the R1b ``mentions`` alias and the legacy ``@名字`` content-parsing
bridge were removed in PR-C). A mail with zero resolvable recipients is
rejected outright — an unaddressed mail is illegal.

Delivery is single-track: like every Member, the adapter subscribes to
MAIL_DELIVERED and fans out copies addressed to its own member id to its
attached sessions. The NotificationService does NOT push mail to chat
sessions — human mail delivery goes through this adapter, so "one
mailbox per Member, delivery via Member" holds for humans too.

See docs/design/members.md (阶段 B).
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from tlon.base.component import Component
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths, paths
from tlon.base.tracing.trace_context import start_span
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.resolver import RecipientResolver
from tlon.organization.messaging.member import Member

if TYPE_CHECKING:
    from tlon.base.config import AgentInfoProvider

logger = get_logger(__name__)

# Default username when the client provides none.
DEFAULT_USERNAME = "guest"

# Prefix of the internal human member-id namespace ("human:<username>").
HUMAN_ID_PREFIX = "human:"

# Resolves a recipient (name, id or full address) to a mail address.
AddressResolver = Callable[[str], str | None]

# Push callback for an attached session (sync or async, like the
# NotificationService event handlers).
EventHandler = Callable[[Event], None | Awaitable[None]]


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
            address_resolver: Recipient name -> mail address resolution
                (injected by the AdapterManager)
        """
        Component.__init__(self, name=f"HumanAdapter:{username}")
        Device.__init__(self, event_bus=event_bus, name=f"HumanAdapter:{username}")
        self._username = username
        self._address_resolver = address_resolver
        self._sinks: dict[str, EventHandler] = {}  # session_id -> push callback

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
    def session_count(self) -> int:
        """Number of currently attached sessions."""
        return len(self._sinks)

    def get_state(self) -> str:
        """Get adapter state: "online" with sessions, "offline" without."""
        return "online" if self._sinks else "offline"

    def attach_session(self, session_id: str, sink: EventHandler) -> None:
        """Attach a client session (refcount +1).

        Args:
            session_id: Session to attach
            sink: Push callback for mail delivered to this human
        """
        self._sinks[session_id] = sink

    def detach_session(self, session_id: str) -> None:
        """Detach a client session (refcount -1).

        Args:
            session_id: Session to detach
        """
        self._sinks.pop(session_id, None)

    async def initialize(self) -> None:
        """Initialize the adapter and subscribe to mail delivery."""
        self.subscribe(EventType.MAIL_DELIVERED, self._on_mail_delivered)
        self._initialized = True
        logger.info("human_adapter_initialized", username=self._username)

    async def shutdown(self) -> None:
        """Shutdown the adapter and release bus subscriptions."""
        self.unsubscribe_all()
        self._initialized = False
        logger.info("human_adapter_shutdown", username=self._username)

    async def _on_mail_delivered(self, event: Event) -> None:
        """Handle mail delivered to this human's inbox.

        The delivery component publishes one MAIL_DELIVERED per recipient;
        only the copy addressed to this member is fanned out to the
        attached sessions.

        Args:
            event: The MAIL_DELIVERED event (dict payload)
        """
        payload = event.payload
        if not isinstance(payload, dict):
            return
        if payload.get("recipient") != self.member_id:
            return

        mail = payload.get("mail")
        for sink in list(self._sinks.values()):
            await self._safe_call(sink, event)

        logger.debug(
            "human_mail_pushed",
            username=self._username,
            mail_id=mail.id if isinstance(mail, Mail) else None,
            session_count=len(self._sinks),
        )

    async def _safe_call(self, sink: EventHandler, event: Event) -> None:
        """Safely call a session sink with error handling.

        A failing sink is logged and does not affect the other sinks.

        Args:
            sink: The session push callback
            event: The event to pass
        """
        try:
            if asyncio.iscoroutinefunction(sink):
                await sink(event)
            else:
                sink(event)
        except Exception as e:
            logger.error(
                "human_mail_sink_error", username=self._username, error=str(e)
            )

    async def speak(
        self,
        content: str,
        to: list[str] | None = None,
        cc: list[str] | None = None,
        subject: str | None = None,
        in_reply_to: str | None = None,
        thread_id: str | None = None,
    ) -> Mail:
        """Speak into Congress: publish the human's mail as MAIL_NEW.

        Recipients come exclusively from the explicit ``to`` / ``cc``
        fields (R2 protocol; the R1b ``mentions`` alias and the legacy
        ``@名字`` bridge were removed in PR-C). Unresolvable recipients
        are dropped with a warning; a mail with zero resolvable
        recipients is rejected outright — an unaddressed mail is
        illegal.

        Args:
            content: Message content
            to: Explicit recipients (raw names, ids or full addresses)
            cc: Carbon-copy recipients (same resolution as ``to``)
            subject: Optional mail subject
            in_reply_to: Id of the mail being replied to (optional)
            thread_id: Thread root id inherited from the upstream mail
                (optional; defaults to this mail's own id)

        Returns:
            The created mail

        Raises:
            ValueError: If no recipient resolves to a mail address
        """
        resolved_to = self._resolve_recipients(to or [])
        resolved_cc = self._resolve_recipients(cc or [])

        if not resolved_to and not resolved_cc:
            raise ValueError(
                "没有可送达的收件人：请在 to 中指定至少一个收件人"
            )

        mail = Mail.create(
            from_address=self.address,
            to=resolved_to,
            cc=resolved_cc,
            subject=subject,
            content=content,
            in_reply_to=in_reply_to,
            thread_id=thread_id,
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
            cc=mail.cc,
        )
        return mail

    def _resolve_recipients(self, raw: list[str]) -> list[str]:
        """Resolve raw recipients (names, ids or full addresses) to mail
        addresses, dropping unresolvable ones and deduplicating.

        Args:
            raw: Raw recipient strings in priority order

        Returns:
            Resolved mail addresses (deduplicated, order preserved)
        """
        resolved: list[str] = []
        for name in raw:
            address = self._address_resolver(name)
            if address is None:
                logger.warning(
                    "recipient_unresolved", username=self._username, recipient=name
                )
                continue
            if address not in resolved:
                resolved.append(address)
        return resolved


class AdapterManager(Component):
    """Manages HumanAdapter lifecycle (username → adapter, refcounted).

    Not part of ServerApp's managed component set: adapters have a dynamic
    lifecycle (attach/detach driven), the manager itself is a regular
    ServerApp component with initialize/shutdown.
    """

    def __init__(
        self,
        event_bus: EventBus,
        agent_info_provider: "AgentInfoProvider",
        tlon_paths: TlonPaths | None = None,
        external_owner: Callable[[str], str | None] | None = None,
    ):
        """Initialize the adapter manager.

        Args:
            event_bus: Event bus adapters publish on
            agent_info_provider: Provider for agent config queries
                (recipient name → address resolution)
            tlon_paths: Path resolver (tests); None uses the singleton
            external_owner: Gateway claim lookup forwarded to the shared
                resolver (issue  egress); None = no gateway claims
        """
        super().__init__(name="AdapterManager")
        self._event_bus = event_bus
        self._paths = tlon_paths or paths
        self._adapters: dict[str, HumanAdapter] = {}  # username -> adapter
        # Single authority for recipient resolution (issue ); the
        # online check closes over ``self._adapters``, so the resolver
        # always sees the live adapter set. MailDelivery delegates to
        # the same instance via ``recipient_resolver``.
        self._resolver = RecipientResolver(
            agent_info_provider=agent_info_provider,
            is_human_online=lambda username: username in self._adapters,
            tlon_paths=self._paths,
            external_owner=external_owner,
        )
        self._sessions: dict[str, str] = {}  # session_id -> username
        self._lock = asyncio.Lock()  # serializes attach/detach

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

    async def attach(
        self, username: str, session_id: str, sink: EventHandler
    ) -> HumanAdapter:
        """Attach a session to the user's adapter, creating it if needed.

        Serialized by a lock: two concurrent first-attaches for the same
        username must not create (and leak) two adapters. First attach
        also creates the human's persistent mailbox — having connected
        once makes the human "known", so mail addressed to them is held
        in their inbox while they are offline (docs/design/mail.md).

        Args:
            username: Human username
            session_id: Session to attach
            sink: Push callback for mail delivered to this human

        Returns:
            The user's adapter (shared across same-name sessions)
        """
        async with self._lock:
            adapter = await self._get_or_create_adapter(username)
            adapter.attach_session(session_id, sink)
            self._sessions[session_id] = username
            logger.info(
                "human_session_attached",
                username=username,
                session_id=session_id,
                session_count=adapter.session_count,
            )
            return adapter

    async def _get_or_create_adapter(self, username: str) -> HumanAdapter:
        """Get or create the user's adapter; caller must hold ``self._lock``.

        First creation also creates the human's persistent mailbox —
        having spoken/connected once makes the human "known", so mail
        addressed to them is held in their inbox while they are offline
        (docs/design/mail.md).
        """
        adapter = self._adapters.get(username)
        if adapter is None:
            adapter = HumanAdapter(username, self._event_bus, self.resolve_address)
            await adapter.initialize()
            self._adapters[username] = adapter
            self._paths.member_mail_dir(adapter.member_id)
            logger.info(
                "human_adapter_created",
                username=username,
                member_id=adapter.member_id,
            )
        return adapter

    async def detach(self, session_id: str) -> None:
        """Detach a session; shutdown the adapter when its last session leaves.

        Args:
            session_id: Session to detach
        """
        async with self._lock:
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
        self,
        session_id: str,
        content: str,
        to: list[str] | None = None,
        cc: list[str] | None = None,
        subject: str | None = None,
        in_reply_to: str | None = None,
        thread_id: str | None = None,
    ) -> Mail | None:
        """Publish a mail from the user owning this session.

        Args:
            session_id: Sender's session ID
            content: Message content
            to: Explicit recipients (raw names, ids or full addresses)
            cc: Carbon-copy recipients
            subject: Optional mail subject
            in_reply_to: Id of the mail being replied to (optional)
            thread_id: Thread root id inherited from the upstream mail
                (optional)

        Returns:
            The created mail, or None if no adapter is attached to
            this session (rejected, logged as warning)

        Raises:
            ValueError: If no recipient resolves to a mail address
        """
        username = self._sessions.get(session_id)
        adapter = self._adapters.get(username) if username else None
        if adapter is None:
            logger.warning("publish_rejected_no_adapter", session_id=session_id)
            return None
        return await adapter.speak(
            content,
            to=to,
            cc=cc,
            subject=subject,
            in_reply_to=in_reply_to,
            thread_id=thread_id,
        )

    async def publish_as(
        self,
        username: str,
        content: str,
        to: list[str] | None = None,
        cc: list[str] | None = None,
        subject: str | None = None,
        in_reply_to: str | None = None,
        thread_id: str | None = None,
    ) -> Mail:
        """Publish a mail as ``human:<username>`` without a client session.

        REST entry point (POST /messages, protocol v2 PR-1): the sender's
        identity comes from the request body rather than an attached
        session. If the human has no live adapter, a transient one is
        created — first contact also creates the persistent mailbox, same
        semantics as the first session attach — and torn down again once
        the mail is out.

        Args:
            username: Human username (sender identity)
            content: Message content
            to: Explicit recipients (raw names, ids or full addresses)
            cc: Carbon-copy recipients
            subject: Optional mail subject
            in_reply_to: Id of the mail being replied to (optional)
            thread_id: Thread root id inherited from the upstream mail
                (optional)

        Returns:
            The created mail

        Raises:
            ValueError: If no recipient resolves to a mail address
        """
        async with self._lock:
            existing = self._adapters.get(username)
            transient = existing is None
            adapter = (
                existing
                if existing is not None
                else await self._get_or_create_adapter(username)
            )

        try:
            return await adapter.speak(
                content,
                to=to,
                cc=cc,
                subject=subject,
                in_reply_to=in_reply_to,
                thread_id=thread_id,
            )
        finally:
            if transient:
                async with self._lock:
                    # A session may have attached concurrently; only tear
                    # down the adapter if it is still session-less.
                    if adapter.session_count == 0:
                        await adapter.shutdown()
                        if self._adapters.get(username) is adapter:
                            del self._adapters[username]
                        logger.info("human_adapter_removed", username=username)

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

    @property
    def recipient_resolver(self) -> RecipientResolver:
        """The shared recipient resolver (single authority, issue ).

        MailDelivery picks it up here so both sides of resolution
        (speak-side names, delivery-side addresses) share one
        implementation of the "known human" rule.
        """
        return self._resolver

    def resolve_address(self, recipient: str) -> str | None:
        """Resolve a recipient to a mail address.

        Delegates to the shared RecipientResolver (mail domain, issue
        ): accepts a full mail address (passed through), an agent
        id, an agent display name, a human's username or the
        "human:<username>" form. A human resolves when they are online
        or known-but-offline with a persistent mailbox — the same rule
        MailDelivery applies on the delivery side, so a resolvable
        recipient here is deliverable there.

        Args:
            recipient: Recipient to resolve

        Returns:
            The member's mail address, or None if unresolvable
        """
        return self._resolver.resolve_address(recipient)

    def username_for(self, session_id: str) -> str | None:
        """Get the username owning a session.

        Args:
            session_id: Session ID

        Returns:
            The username, or None if the session is not attached
        """
        return self._sessions.get(session_id)
