"""Member Directory for Tlon Server.

Aggregates and provides information about all members in the system:
- Agents (via the members facade, including sleeping ones)
- Humans (via the events facade — one online adapter per username)

This is the address book (通讯录用): member listing and lookup only.
Recipient→address resolution lives in the shared RecipientResolver
(organization/mail/resolver.py, issue ); the runtime touchpoints are
reached through the tlon.api facades (ADR 0019).
"""

from tlon.api.events import EventsFacade
from tlon.api.members import MemberInfo, MembersFacade
from tlon.base.logger import get_logger

logger = get_logger(__name__)


class MemberDirectory:
    """Discovers and aggregates all Congress members.

    Responsibilities:
    - Aggregate agents from the members facade
    - Aggregate online humans from the events facade
    - Provide member lookup

    Note: This performs real-time aggregation on each call.
    If performance becomes an issue, consider adding caching.

    Example:
        >>> directory = MemberDirectory(members=members_facade, events=events_facade)
        >>> all_members = directory.list_all()
        >>> agents = directory.list_agents()
        >>> humans = directory.list_humans()
    """

    def __init__(
        self,
        *,
        members: MembersFacade,
        events: EventsFacade,
    ):
        """Initialize the member directory.

        Args:
            members: Members facade (agent listing/lookup)
            events: Events facade (online human presence)
        """
        self._members = members
        self._events = events

        logger.info("member_directory_initialized")

    def list_all(self) -> list[MemberInfo]:
        """Get all members (agents + online humans).

        This method performs real-time aggregation from both facades.

        Returns:
            List of all members
        """
        members: list[MemberInfo] = []
        members.extend(self.list_agents())
        members.extend(self.list_humans())
        return members

    def list_agents(self) -> list[MemberInfo]:
        """Get all agent members.

        Returns:
            List of agent members
        """
        return self._members.list_agent_members()

    def list_humans(self) -> list[MemberInfo]:
        """Get all online human members (one per username).

        Returns:
            List of human members, one per online adapter
        """
        return self._events.list_online_humans()

    def get_member(self, member_id: str) -> MemberInfo | None:
        """Get a specific member by ID.

        Args:
            member_id: The member ID (agent ID or "human:<username>")

        Returns:
            The member if found, None otherwise
        """
        # Online human adapter?
        human = self._events.get_online_human(member_id.removeprefix("human:"))
        if human is not None and human.id == member_id:
            return human

        # Check agents
        return self._members.get_agent_member(member_id)

    def get_member_names(self) -> list[str]:
        """Get names of all members.

        Returns:
            List of member display names
        """
        return [m.name for m in self.list_all()]

    def count_agents(self) -> int:
        """Get the number of agents.

        Returns:
            Number of registered agents
        """
        return len(self._members.list_ids())

    def count_humans(self) -> int:
        """Get the number of online human members.

        Returns:
            Number of online human adapters (one per username)
        """
        return len(self._events.list_online_humans())

    def count_all(self) -> int:
        """Get the total number of members.

        Returns:
            Total member count (agents + online humans)
        """
        return self.count_agents() + self.count_humans()
