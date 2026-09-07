"""External gateway registry: claimed external mail addresses (issue ).

The egress half of the "外部来信" mechanism (docs/design/mail.md
「外部收件人与出口方向」). An external recipient address is normally
unresolvable and bounces; a gateway component may *claim* an external
address here, after which the shared ``RecipientResolver`` resolves it
to the gateway's member id and delivery proceeds normally — the mail
lands in the gateway's mailbox (the outbound audit trail) and the
gateway's ``MAIL_DELIVERED`` handler performs the actual egress.

Unclaimed external recipients keep bouncing — the claim table is the
one controlled exception, consulted by the same single-authority
resolver (), not a parallel resolution track.

A plain data component (no dependencies): the registry is constructed
at assembly time and its ``owner_of`` is constructor-injected wherever
a resolver lives.
"""

from tlon.base.logger import get_logger

logger = get_logger(__name__)


class ExternalGatewayRegistry:
    """Maps claimed external mail addresses to their gateway member id.

    Example:
        >>> registry = ExternalGatewayRegistry()
        >>> registry.claim("notifications@github.com", "gateway:github")
        >>> registry.owner_of("notifications@github.com")
        'gateway:github'
        >>> registry.owner_of("nobody@example.com") is None
        True
    """

    def __init__(self) -> None:
        self._owners: dict[str, str] = {}  # external address -> gateway member id

    def claim(self, address: str, member_id: str) -> None:
        """Claim an external address for a gateway member.

        Re-claiming an already-claimed address overwrites the owner and
        logs a warning — two gateways owning one address would split the
        outbound stream, so the collision must be visible.

        Args:
            address: The external mail address to claim
            member_id: The claiming gateway's member id
        """
        previous = self._owners.get(address)
        if previous is not None:
            logger.warning(
                "gateway_address_reclaimed",
                address=address,
                previous=previous,
                member_id=member_id,
            )
        else:
            logger.info("gateway_address_claimed", address=address, member_id=member_id)
        self._owners[address] = member_id

    def owner_of(self, address: str) -> str | None:
        """Return the gateway member id claiming an address, or None.

        Args:
            address: The mail address to look up

        Returns:
            The claiming gateway's member id, or None when unclaimed
        """
        return self._owners.get(address)
