"""Guardian-aware agent info provider (see docs/design/mail.md — Adam can
receive mail).

Adam is a built-in member with no config file (``~/.tlon/adam/config/
agent.yaml`` does not exist), so the plain ConfigService cannot resolve
``adam@tlon.internal`` and mail to the guardian bounced. This wrapper
delegates every query to the injected provider and answers exactly one
extra id: ``get_config("adam")`` returns a synthesized AgentConfig.

``get_all_ids`` is *not* extended — Adam stays out of the roster and
every enumeration-driven scan (trust/ledger/spawn), keeping those
surfaces exactly as they were.

The wrapper belongs to the runtime's mail side (it exists so MailDelivery
can resolve the guardian): ``build_runtime()`` wraps the ConfigService
once and hands the same instance to both the AdapterManager (speak-side
resolution) and MailDelivery (delivery-side resolution), which share one
RecipientResolver — one wrap, both directions.
"""

from tlon.base.config import AgentConfig, AgentInfoProvider
from tlon.base.logger import get_logger

logger = get_logger(__name__)

#: The built-in guardian's member id (matches ``make_adam``).
ADAM_MEMBER_ID = "adam"

#: The synthesized config that makes Adam resolvable. Read-only: Adam has
#: no config file and nothing may write one through this provider.
_ADAM_CONFIG = AgentConfig(id=ADAM_MEMBER_ID, name="Adam")


class GuardianAgentInfoProvider(AgentInfoProvider):
    """AgentInfoProvider that additionally resolves the built-in guardian.

    Every query delegates to the wrapped provider; the single difference
    is that ``get_config("adam")`` answers a synthesized config, making
    ``adam@tlon.internal`` deliverable on both resolution directions.
    Addressing by display name ("Adam") is intentionally unsupported —
    ``get_all_ids`` delegates unchanged, so name lookup and
    enumeration-driven scans never see Adam.

    Example:
        >>> provider = GuardianAgentInfoProvider(config_service)
        >>> provider.get_config("adam") is not None
        True
        >>> "adam" in provider.get_all_ids()
        False
    """

    def __init__(self, delegate: AgentInfoProvider):
        """Initialize the wrapper.

        Args:
            delegate: The real provider (ConfigService in production)
        """
        self._delegate = delegate

    def get_config(self, agent_id: str) -> AgentConfig | None:
        """Get agent configuration; "adam" resolves to the guardian."""
        if agent_id == ADAM_MEMBER_ID:
            return _ADAM_CONFIG
        return self._delegate.get_config(agent_id)

    def get_all_ids(self) -> set[str]:
        """All configured agent ids — Adam deliberately excluded."""
        return self._delegate.get_all_ids()

    async def reload_agent(self, agent_id: str) -> bool:
        """Forward hot-reload to the delegate (Adam has nothing to reload)."""
        if agent_id == ADAM_MEMBER_ID:
            return False
        return await self._delegate.reload_agent(agent_id)

    def delete_agent(self, agent_id: str) -> None:
        """Forward deletion to the delegate; the guardian cannot be deleted."""
        if agent_id == ADAM_MEMBER_ID:
            raise NotImplementedError("the built-in guardian cannot be deleted")
        self._delegate.delete_agent(agent_id)
