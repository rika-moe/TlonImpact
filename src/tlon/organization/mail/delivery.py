"""Mail delivery for Tlon (the router's successor, docs/design/mail.md).

The MailDelivery component subscribes to MAIL_NEW and, for each mail:

1. expands ``all@tlon.internal`` into every registered agent plus every
   known human (online, or offline with a persistent mailbox);
2. resolves each recipient address to a member (agents via the config
   provider, humans via the online adapters or their persistent
   mailbox);
3. writes the mail to the sender's ``sent.jsonl`` and to every valid
   recipient's ``inbox.jsonl`` (mailboxes are resolved via
   ``paths.member_mail_dir`` and cached on demand);
4. publishes one MAIL_DELIVERED event per recipient, carrying the Mail
   and the recipient's member id; a mail carrying an ``endorsing``
   reference additionally publishes one MAIL_ENDORSED (the reputation
   signal, issue ).

Recipients that resolve to nobody are collected and answered with a
bounce mail from ``postmaster@tlon.internal`` to the sender — the error
lands in the sender's inbox, so an LLM can correct the address itself.

Delivery is idempotent within the process: a republished ``MAIL_NEW``
(for the same mail id) does not write a second inbox/sent copy or emit
a second ``MAIL_DELIVERED`` — the component remembers recently handled
``(mail_id, member)`` pairs in a bounded in-memory set.
"""

from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from tlon.base.component import Component
from tlon.base.device import Device
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths, paths
from tlon.base.tracing.trace_context import SpanCtx, continue_trace, start_span
from tlon.organization.mail.mail import (
    ALL_ADDRESS,
    POSTMASTER_ADDRESS,
    Mail,
    address_for_member,
)
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.messaging.human_adapter import AdapterManager

if TYPE_CHECKING:
    from tlon.base.config import AgentInfoProvider

logger = get_logger(__name__)

#: Bound on the in-memory idempotency set (oldest entries evicted).
_SEEN_CAP = 4096

#: Bound on the on-demand Mailbox cache (oldest entries evicted).
#: An evicted member's Mailbox is simply rebuilt on next delivery —
#: the instance is a stateless handle, and mutual exclusion comes from
#: the path-shared lock in ``mailbox._LOCKS``, not from the instance.
_MAILBOXES_CAP = 4096


class MailDelivery(Component, Device):
    """Delivers mail from the bus into per-member mailboxes.

    Stateless apart from the on-demand Mailbox cache: all routing
    information comes from the injected AgentInfoProvider (registered
    agents, including sleeping ones), the AdapterManager (online
    humans), and the persistent mailbox layout (known humans).

    Dependencies (constructor injected):
        - event_bus: EventBus for subscription/publishing
        - agent_info_provider: AgentInfoProvider for agent existence and
          the ``all@`` expansion
        - adapter_manager: AdapterManager for online-human resolution
        - tlon_paths: TlonPaths instance (defaults to module singleton)

    Example:
        >>> delivery = MailDelivery(event_bus, config_service, adapter_manager)
        >>> await delivery.initialize()  # Begins listening for MAIL_NEW
    """

    def __init__(
        self,
        event_bus: EventBus,
        agent_info_provider: "AgentInfoProvider",
        adapter_manager: AdapterManager,
        tlon_paths: TlonPaths | None = None,
    ):
        """Initialize the delivery component.

        Args:
            event_bus: Event bus for subscribing and publishing
            agent_info_provider: Provider for agent configuration queries
            adapter_manager: Manager for online human adapters
            tlon_paths: Path resolver (tests); None uses the singleton
        """
        Component.__init__(self, name="MailDelivery")
        Device.__init__(self, event_bus=event_bus, name="MailDelivery")
        self._agent_info = agent_info_provider
        self._adapter_manager = adapter_manager
        # Shared recipient resolver (single authority, issue ):
        # address -> member resolution delegates to the same instance
        # the speak side uses, so the "known human" rule exists once.
        self._resolver = adapter_manager.recipient_resolver
        self._paths = tlon_paths or paths
        # member_id -> Mailbox, created lazily on first delivery;
        # bounded LRU (_MAILBOXES_CAP, least recently used evicted)
        self._mailboxes: OrderedDict[str, Mailbox] = OrderedDict()
        # Recently handled "mail_id|member" keys (in-process idempotency)
        self._seen: OrderedDict[str, None] = OrderedDict()

        logger.info("mail_delivery_constructed")

    async def initialize(self) -> None:
        """Start the delivery component by subscribing to MAIL_NEW."""
        self.subscribe(EventType.MAIL_NEW, self._on_mail_new)
        logger.info("mail_delivery_initialized")
        self._initialized = True

    async def shutdown(self) -> None:
        """Stop the delivery component."""
        self.unsubscribe_all()
        self._initialized = False
        logger.info("mail_delivery_shutdown")

    async def _on_mail_new(self, event: Event) -> None:
        """Handle a new mail: persist, expand, deliver, bounce."""
        mail = event.payload
        if not isinstance(mail, Mail):
            logger.warning("mail_delivery_bad_payload", payload_type=type(mail).__name__)
            return

        attributes = {"mail_id": mail.id, "sender": mail.from_address}
        if event.trace_id:
            with continue_trace(event.trace_id, event.span_id), start_span(
                "mail.delivery", source="mail_delivery", attributes=attributes
            ) as ctx:
                await self._deliver(mail, ctx)
        else:
            with start_span(
                "mail.delivery", source="mail_delivery", attributes=attributes
            ) as ctx:
                await self._deliver(mail, ctx)

    async def _deliver(self, mail: Mail, ctx: SpanCtx) -> None:
        """Persist and fan out one mail within a tracing span."""
        try:
            sender_id = self._resolve_address(mail.from_address)

            # Sender's own copy (postmaster/system senders have no mailbox)
            if sender_id is not None and not self._seen_before(f"{mail.id}|sent|{sender_id}"):
                await self._mailbox_for(sender_id).append_sent(mail)

            unknown: list[str] = []
            delivered: list[str] = []
            for address in self._expand_recipients(mail):
                member_id = self._resolve_address(address)
                if member_id is None:
                    unknown.append(address)
                    continue
                if self._seen_before(f"{mail.id}|inbox|{member_id}"):
                    continue  # republished MAIL_NEW: already delivered
                await self._mailbox_for(member_id).append_inbox(mail)
                delivered.append(member_id)
                await self._publish_delivered(mail, member_id, ctx)

            logger.info(
                "mail_delivered",
                mail_id=mail.id,
                recipients=delivered,
                unknown=unknown,
            )

            # Endorsement signal (issue ): a mail carrying an
            # `endorsing` reference is a public reputation fact; publish
            # it once per mail, independent of the recipient fan-out.
            if mail.endorsing and not self._seen_before(f"{mail.id}|endorsed"):
                await self._publish_endorsed(mail, sender_id, ctx)

            if unknown and sender_id is not None:
                if not self._seen_before(f"{mail.id}|bounce|{sender_id}"):
                    await self._bounce(mail, unknown, sender_id, ctx)
            elif unknown:
                logger.warning(
                    "mail_bounce_dropped",
                    mail_id=mail.id,
                    unknown=unknown,
                    reason="sender has no mailbox",
                )
        except Exception as e:
            logger.error(
                "mail_delivery_failed",
                mail_id=mail.id,
                error=str(e),
                exc_info=True,
            )

    def _expand_recipients(self, mail: Mail) -> list[str]:
        """Expand to+cc into concrete addresses (all@ -> every member)."""
        addresses: list[str] = []
        for address in [*mail.to, *mail.cc]:
            if address == ALL_ADDRESS:
                addresses.extend(self._expand_all())
            else:
                addresses.append(address)
        # Dedup preserving order; a recipient gets one copy
        return list(dict.fromkeys(addresses))

    def _expand_all(self) -> list[str]:
        """all@tlon.internal: all registered agents + all known humans.

        Known humans = online adapters ∪ humans with a persistent
        mailbox (docs/design/mail.md: "有邮箱的人类") — offline humans
        are included; their copy waits in their inbox.
        """
        addresses = [
            address_for_member(agent_id)
            for agent_id in sorted(self._agent_info.get_all_ids())
        ]
        addresses.extend(
            address_for_member(adapter.member_id)
            for adapter in self._adapter_manager.list_online()
        )
        addresses.extend(
            address_for_member(human_id) for human_id in self._paths.known_human_ids()
        )
        return addresses

    def _resolve_address(self, address: str) -> str | None:
        """Resolve a mail address to a member id, or None if unknown.

        Delegates to the shared RecipientResolver (issue ): agents
        (registered, sleeping included) win over humans on a colliding
        local part; a human resolves when they are online or have a
        persistent mailbox — mail to a known-but-offline human waits in
        their inbox (docs/design/mail.md 人类侧: 离线时邮件留在邮箱).
        """
        return self._resolver.resolve_member(address)

    def _mailbox_for(self, member_id: str) -> Mailbox:
        """Return the cached Mailbox for a member, creating it on demand.

        The cache is a bounded LRU (``_MAILBOXES_CAP``): hits are
        refreshed to most-recently-used, and the least recently used
        entry is evicted on overflow. Eviction is safe — a Mailbox is a
        stateless handle and its mutual exclusion comes from the
        path-shared lock in ``mailbox._LOCKS``, so a rebuilt instance
        over the same directory acquires the same lock.
        """
        mailbox = self._mailboxes.get(member_id)
        if mailbox is not None:
            self._mailboxes.move_to_end(member_id)
            return mailbox
        mailbox = Mailbox(self._paths.member_mail_dir(member_id))
        self._mailboxes[member_id] = mailbox
        if len(self._mailboxes) > _MAILBOXES_CAP:
            self._mailboxes.popitem(last=False)
        return mailbox

    def _seen_before(self, key: str) -> bool:
        """Record a delivery key; True when it was already handled.

        In-process idempotency for republished ``MAIL_NEW`` events:
        the first delivery of a (mail, member) pair wins, replays are
        skipped. The set is bounded (``_SEEN_CAP``, oldest evicted) —
        a restart or an evicted key simply falls back to redelivery,
        which the append-only mailbox tolerates.
        """
        if key in self._seen:
            return True
        self._seen[key] = None
        if len(self._seen) > _SEEN_CAP:
            self._seen.popitem(last=False)
        return False

    async def _publish_delivered(
        self, mail: Mail, member_id: str, ctx: SpanCtx | None = None
    ) -> None:
        """Publish one MAIL_DELIVERED event for one recipient."""
        event_kwargs: dict[str, Any] = {
            "type": EventType.MAIL_DELIVERED,
            "payload": {"mail": mail, "recipient": member_id},
            "source": "mail_delivery",
        }
        if ctx and ctx.trace_id:
            event_kwargs["trace_id"] = ctx.trace_id
            event_kwargs["span_id"] = ctx.span_id
            event_kwargs["parent_span_id"] = ctx.parent_span_id
        await self.publish(Event(**event_kwargs))

    async def _publish_endorsed(
        self, mail: Mail, sender_id: str | None, ctx: SpanCtx
    ) -> None:
        """Publish MAIL_ENDORSED for a mail carrying an endorsement.

        Payload: ``endorser`` (sender member id, or the raw address when
        the sender resolves to no member), ``mail_id`` (the endorsed
        mail), ``thread_id``, and ``author`` (the endorsed mail's author
        address, derived from the endorser's own mailbox when findable —
        the executor guarantees the mail sits there for agent senders).
        """
        author: str | None = None
        if sender_id is not None:
            endorsed = await self._mailbox_for(sender_id).find(mail.endorsing or "")
            if endorsed is not None:
                author = endorsed.from_address
        event_kwargs: dict[str, Any] = {
            "type": EventType.MAIL_ENDORSED,
            "payload": {
                "endorser": sender_id or mail.from_address,
                "mail_id": mail.endorsing,
                "author": author,
                "thread_id": mail.thread_id,
            },
            "source": "mail_delivery",
        }
        if ctx and ctx.trace_id:
            event_kwargs["trace_id"] = ctx.trace_id
            event_kwargs["span_id"] = ctx.span_id
            event_kwargs["parent_span_id"] = ctx.parent_span_id
        await self.publish(Event(**event_kwargs))
        logger.info(
            "mail_endorsed",
            mail_id=mail.id,
            endorser=sender_id or mail.from_address,
            endorsed_id=mail.endorsing,
            author=author,
        )

    async def _bounce(
        self, mail: Mail, unknown: list[str], sender_id: str, ctx: SpanCtx
    ) -> None:
        """Return a bounce mail to the sender for unknown recipients."""
        bounce = Mail.create(
            from_address=POSTMASTER_ADDRESS,
            to=[mail.from_address],
            subject=f"Undeliverable: {mail.subject or mail.id}",
            content=(
                "以下收件人地址不存在，邮件未送达：\n"
                + "\n".join(f"- {address}" for address in unknown)
            ),
            in_reply_to=mail.id,
            thread_id=mail.thread_id,
        )
        await self._mailbox_for(sender_id).append_inbox(bounce)
        await self._publish_delivered(bounce, sender_id, ctx)
        logger.info(
            "mail_bounced",
            mail_id=mail.id,
            bounce_id=bounce.id,
            unknown=unknown,
        )
