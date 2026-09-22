"""Recipient resolution: the single authority for name/address mapping.

Both directions of recipient resolution live here (issue  — the
adapter-side ``AdapterManager.resolve_address`` and the delivery-side
``MailDelivery._resolve_address`` used to be two tracks whose rules had
to be kept in sync by hand;  was a drift bug):

- ``resolve_address``: raw recipient (agent id, agent display name,
  human username, ``human:<username>`` or full address) -> mail address.
  Used by the adapter side (HumanAdapter speaks with raw names).
- ``resolve_member``: mail address -> member id. Used by the delivery
  side (MailDelivery routes concrete addresses). External addresses
  resolve only via a gateway claim (``external_owner``, issue ).

The "known human" rule — online OR offline with a persistent mailbox —
is written exactly once (``_known_human``), so a recipient resolvable
on the speak side is deliverable on the delivery side.

Capabilities are constructor-injected (agent queries via
AgentInfoProvider, human online check via a plain callable, mailbox
layout via TlonPaths), so this module stays inside the organization
domain's dependency contract (base only).
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import (
    HUMAN_ID_PREFIX,
    address_for_member,
    is_mail_address,
    member_id_for_address,
)

if TYPE_CHECKING:
    from tlon.base.config import AgentInfoProvider

logger = get_logger(__name__)


class RecipientResolver:
    """Single authority for recipient name/address resolution.

    Stateless apart from the injected capabilities: all routing
    information comes from the AgentInfoProvider (registered agents,
    including sleeping ones), the online check (online humans), and the
    persistent mailbox layout (known-but-offline humans).

    Dependencies (constructor injected):
        - agent_info_provider: AgentInfoProvider for agent existence,
          display-name lookup and id enumeration
        - is_human_online: username -> True when a live adapter exists
        - tlon_paths: TlonPaths instance (persistent mailbox layout)
        - external_owner: external address -> claiming gateway member id
          (ExternalGatewayRegistry.owner_of; None = no gateway claims,
          external recipients always bounce)

    Example:
        >>> resolver = RecipientResolver(
        ...     config_service,
        ...     lambda name: adapter_manager.get_adapter(name) is not None,
        ... )
        >>> resolver.resolve_address("谢云舒")  # agent display name
        'xie-yunshu@tlon.internal'
    """

    def __init__(
        self,
        agent_info_provider: "AgentInfoProvider",
        is_human_online: Callable[[str], bool],
        tlon_paths: TlonPaths,
        external_owner: Callable[[str], str | None] | None = None,
    ):
        """Initialize the resolver.

        Args:
            agent_info_provider: Provider for agent configuration queries
            is_human_online: Online check for a human username
            tlon_paths: Path resolver (persistent mailbox layout)
            external_owner: Gateway claim lookup (address -> gateway
                member id); None keeps external recipients unresolvable
        """
        self._agent_info = agent_info_provider
        self._is_human_online = is_human_online
        self._paths = tlon_paths
        self._external_owner = external_owner

    def resolve_address(self, recipient: str) -> str | None:
        """Resolve a raw recipient to a mail address.

        Accepts a full mail address (passed through), an agent id, an
        agent display name, a human's username or the
        "human:<username>" form. A human resolves when they are known —
        online or with a persistent mailbox (``_known_human``), the same
        rule ``resolve_member`` applies on the delivery side.

        Args:
            recipient: Recipient to resolve

        Returns:
            The member's mail address, or None if unresolvable
        """
        name = recipient.lstrip("@")
        if is_mail_address(name):
            return name

        # Agent id direct hit, then agent display name lookup
        if self._agent_info.get_config(name) is not None:
            return address_for_member(name)
        matches = [
            agent_id
            for agent_id in self._agent_info.get_all_ids()
            if (config := self._agent_info.get_config(agent_id))
            and config.name == name
        ]
        if len(matches) == 1:
            return address_for_member(matches[0])
        if len(matches) > 1:
            # Display names are the addressing surface (ADR 0006 member
            # model); a collision must not silently pick the first one.
            # Refuse explicitly so the caller can surface the ambiguity.
            logger.warning(
                "recipient_ambiguous_display_name",
                recipient=name,
                matches=matches,
            )
            return None

        # Human ("alice" or "human:alice"), online or known-offline
        username = (
            name[len(HUMAN_ID_PREFIX):] if name.startswith(HUMAN_ID_PREFIX) else name
        )
        if self._known_human(username):
            return address_for_member(f"{HUMAN_ID_PREFIX}{username}")

        return None

    def resolve_member(self, address: str) -> str | None:
        """Resolve a mail address to a member id, or None if unknown.

        Agents (registered, sleeping included) win over humans on a
        colliding local part. A human resolves when they are known —
        online or with a persistent mailbox (``_known_human``) — so mail
        to a known-but-offline human waits in their inbox
        (docs/design/mail.md 人类侧: 离线时邮件留在邮箱).

        External (foreign-domain) addresses resolve only when a gateway
        has claimed them in the external gateway registry (issue 
        egress); unclaimed external recipients stay unknown and bounce.

        Args:
            address: A mail address, internal or external

        Returns:
            The member id (agent id, "human:<username>" or gateway id),
            or None
        """
        local = member_id_for_address(address)
        if local is None:
            if self._external_owner is not None:
                return self._external_owner(address)
            return None
        if self._agent_info.get_config(local) is not None:
            return local
        if self._known_human(local):
            return f"{HUMAN_ID_PREFIX}{local}"
        return None

    def _known_human(self, username: str) -> bool:
        """The single "known human" rule: online OR has a mailbox.

        Mail addressed to a known-but-offline human is held in their
        inbox (docs/design/mail.md). This is the one and only place the
        rule lives — both resolution directions share it.
        """
        if self._is_human_online(username):
            return True
        return (
            self._paths.member_mail_dir_existing(f"{HUMAN_ID_PREFIX}{username}")
            is not None
        )
