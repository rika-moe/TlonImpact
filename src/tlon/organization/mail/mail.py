"""Mail data model and address rules for Tlon (Republic of Letters).

Addresses live in the ``tlon.internal`` domain. Member ids (as defined by
members.md: agents use their bare id, humans use ``human:<username>``) map
onto mail addresses:

- agent ``gu-yanxi``     -> ``gu-yanxi@tlon.internal``
- human ``human:alice`` -> ``alice@tlon.internal``

Threads are linked by id, not by subject: ``in_reply_to`` points at the
upstream mail and ``thread_id`` is the id of the thread's root mail
(inherited on reply).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

MAIL_DOMAIN = "tlon.internal"
ALL_ADDRESS = f"all@{MAIL_DOMAIN}"
POSTMASTER_ADDRESS = f"postmaster@{MAIL_DOMAIN}"

HUMAN_ID_PREFIX = "human:"


def is_mail_address(value: str) -> bool:
    """Check whether a value has the shape of a mail address.

    Args:
        value: The string to check

    Returns:
        True if the value is a non-empty ``local@domain`` address
    """
    local, sep, domain = value.partition("@")
    return bool(sep) and bool(local) and bool(domain) and "@" not in domain


def address_for_member(member_id: str) -> str:
    """Map a member id to its mail address.

    Args:
        member_id: Agent id (e.g. ``gu-yanxi``) or human id
            (``human:<username>``)

    Returns:
        The member's address in the tlon.internal domain
    """
    local = (
        member_id[len(HUMAN_ID_PREFIX):]
        if member_id.startswith(HUMAN_ID_PREFIX)
        else member_id
    )
    return f"{local}@{MAIL_DOMAIN}"


def member_id_for_address(address: str) -> str | None:
    """Map a mail address back to a member id (local part).

    Note that the mapping is lossy: a bare local part cannot be told apart
    as agent or human; the local part is returned as-is.

    Args:
        address: A mail address

    Returns:
        The local part for tlon.internal addresses, None for foreign
        domains or malformed addresses
    """
    if not is_mail_address(address):
        return None
    local, _, domain = address.partition("@")
    if domain != MAIL_DOMAIN:
        return None
    return local


def _dedup(addresses: list[str]) -> list[str]:
    """Remove duplicates while preserving order."""
    return list(dict.fromkeys(addresses))


@dataclass
class Mail:
    """A single mail in the Republic of Letters.

    Attributes:
        id: Unique mail identifier (uuid hex)
        from_address: Sender address
        to: Primary recipient addresses
        cc: Carbon-copy recipient addresses
        subject: Optional subject (LLM communication often needs none)
        content: Mail body (text)
        in_reply_to: Id of the upstream mail being replied to (optional)
        thread_id: Id of the thread's root mail
        forward_of: Id of the original mail this one forwards (optional)
        endorsing: Id of the mail this one endorses ("this mail helped
            me") — the endorsement signal (optional)
        timestamp: When the mail was created (ISO 8601 string)
    """

    id: str
    from_address: str
    to: list[str]
    cc: list[str]
    subject: str | None
    content: str
    in_reply_to: str | None
    thread_id: str
    forward_of: str | None
    timestamp: str
    endorsing: str | None = None

    @classmethod
    def create(
        cls,
        from_address: str,
        content: str,
        to: list[str] | None = None,
        cc: list[str] | None = None,
        subject: str | None = None,
        in_reply_to: str | None = None,
        thread_id: str | None = None,
        forward_of: str | None = None,
        endorsing: str | None = None,
    ) -> "Mail":
        """Factory method to create a new mail with auto-generated id.

        For a new thread, omit ``thread_id`` — it defaults to the mail's
        own id. For a reply, pass ``in_reply_to`` plus the ``thread_id``
        inherited from the upstream mail.

        Args:
            from_address: Sender address
            content: Mail body (must be non-empty)
            to: Primary recipients (deduplicated, order preserved)
            cc: Carbon-copy recipients (deduplicated, order preserved)
            subject: Optional subject
            in_reply_to: Id of the mail being replied to
            thread_id: Thread root id (defaults to this mail's own id)
            forward_of: Id of the mail being forwarded
            endorsing: Id of the mail this one endorses (optional)

        Returns:
            A new Mail instance

        Raises:
            ValueError: If content is empty, there is no recipient, or any
                address is malformed
        """
        if not content.strip():
            raise ValueError("Mail content must not be empty")

        to = _dedup(to or [])
        cc = _dedup(cc or [])
        if not to and not cc:
            raise ValueError("Mail must have at least one recipient in to or cc")

        for address in [from_address, *to, *cc]:
            if not is_mail_address(address):
                raise ValueError(f"Invalid mail address: {address!r}")

        mail_id = uuid.uuid4().hex
        return cls(
            id=mail_id,
            from_address=from_address,
            to=to,
            cc=cc,
            subject=subject,
            content=content,
            in_reply_to=in_reply_to,
            thread_id=thread_id or mail_id,
            forward_of=forward_of,
            endorsing=endorsing,
            timestamp=datetime.now(UTC).isoformat(),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize mail to dictionary.

        Returns:
            Dictionary representation of the mail
        """
        return {
            "id": self.id,
            "from_address": self.from_address,
            "to": self.to,
            "cc": self.cc,
            "subject": self.subject,
            "content": self.content,
            "in_reply_to": self.in_reply_to,
            "thread_id": self.thread_id,
            "forward_of": self.forward_of,
            "endorsing": self.endorsing,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Mail":
        """Deserialize mail from dictionary.

        Tolerates missing optional fields (subject, cc, in_reply_to,
        forward_of, endorsing, thread_id).

        Args:
            data: Dictionary containing mail data

        Returns:
            A Mail instance
        """
        return cls(
            id=data["id"],
            from_address=data["from_address"],
            to=list(data.get("to") or []),
            cc=list(data.get("cc") or []),
            subject=data.get("subject"),
            content=data["content"],
            in_reply_to=data.get("in_reply_to"),
            thread_id=data.get("thread_id") or data["id"],
            forward_of=data.get("forward_of"),
            timestamp=data["timestamp"],
            endorsing=data.get("endorsing"),
        )

    def involves(self, address: str) -> bool:
        """Check whether an address is the sender or a recipient.

        Used for TUI / push-notification filtering.

        Args:
            address: The address to check

        Returns:
            True if the address sends or receives this mail
        """
        return address == self.from_address or address in self.to or address in self.cc
