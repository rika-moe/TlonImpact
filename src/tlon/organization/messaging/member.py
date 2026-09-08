"""Member interface for Congress bus citizens.

See docs/design/members.md for the full design.
"""

from abc import abstractmethod

from tlon.base.component import Component
from tlon.base.device import Device
from tlon.organization.mail.mail import address_for_member


class Member(Component, Device):
    """A bus citizen of Congress: has an identity, receives MAIL_DELIVERED,
    publishes MAIL_NEW.

    Member makes the implicit "who can speak" contract explicit. Unification
    happens at the Member layer, not the Agent layer: an AdapterAgent (human
    presence) has identity, lifecycle and bus I/O without going through the
    LLM / memory / Docker machinery of a cognitive member (MemberRuntime).

    Following project convention, Member defines no ``__init__`` — subclasses
    call ``Component.__init__(self, name=...)`` and
    ``Device.__init__(self, event_bus=..., name=...)`` explicitly (see
    Agent and MailDelivery).

    See docs/design/members.md.
    """

    @property
    @abstractmethod
    def member_id(self) -> str:
        """Unique member ID (e.g. "xie-yunshu" / "human:alice")."""

    @property
    def address(self) -> str:
        """The member's mail address, derived from ``member_id``."""
        return address_for_member(self.member_id)

    @abstractmethod
    def get_state(self) -> str:
        """Get member state as string."""
