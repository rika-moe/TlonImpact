"""Resolve aliases using configured agents, registered humans and gateway claims."""

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
from tlon.organization.mail.store import MailStore

if TYPE_CHECKING:
    from tlon.base.config import AgentInfoProvider

logger = get_logger(__name__)


class RecipientResolver:
    """Map aliases to addresses and addresses to member identities.

    Configuration includes sleeping agents. Humans become known through SQLite
    registration, independent of active sessions. Retirement is checked by the
    post office, including transactionally at local admission.

    A supplied human_ids snapshot makes resolution database-free for a batch.
    Without a snapshot, callers must run these synchronous queries in a worker.
    """

    def __init__(
        self,
        agent_info_provider: "AgentInfoProvider",
        tlon_paths: TlonPaths,
        external_owner: Callable[[str], str | None] | None = None,
    ) -> None:
        self._agent_info = agent_info_provider
        self._store = MailStore(tlon_paths.mail_database_file())
        self._external_owner = external_owner

    def resolve_address(self, recipient: str, *, human_ids: set[str] | None = None) -> str | None:
        """Resolve an explicit address, agent id/display name, or human name.

        Full addresses still require existence validation through resolve_member.
        Ambiguous agent display names are refused rather than choosing a member.
        """
        name = recipient.lstrip("@")
        if is_mail_address(name):
            return name
        if self._agent_info.get_config(name) is not None:
            return address_for_member(name)
        matches = [
            agent_id
            for agent_id in self._agent_info.get_all_ids()
            if (config := self._agent_info.get_config(agent_id)) and config.name == name
        ]
        if len(matches) == 1:
            return address_for_member(matches[0])
        if len(matches) > 1:
            logger.warning("recipient_ambiguous_display_name", recipient=name, matches=matches)
            return None
        username = name.removeprefix(HUMAN_ID_PREFIX)
        if self._known_human(username, human_ids):
            return address_for_member(f"{HUMAN_ID_PREFIX}{username}")
        return None

    def resolve_member(self, address: str, *, human_ids: set[str] | None = None) -> str | None:
        """Find the member or registered gateway owning an address.

        Configured agents take precedence over humans with the same local part.
        """
        local = member_id_for_address(address)
        if local is None:
            return self._external_owner(address) if self._external_owner else None
        if self._agent_info.get_config(local) is not None:
            return local
        if self._known_human(local, human_ids):
            return f"{HUMAN_ID_PREFIX}{local}"
        return None

    def _known_human(self, username: str, human_ids: set[str] | None) -> bool:
        member_id = f"{HUMAN_ID_PREFIX}{username}"
        return (
            member_id in human_ids
            if human_ids is not None
            else self._store.member_exists(member_id)
        )
