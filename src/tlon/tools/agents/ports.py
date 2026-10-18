"""Ports for the agent lifecycle tools (dependency inversion, ).

The spawn, self-scheduling and directory tools live in ``tlon.tools`` while their implementation
(``AgentRegistry``) lives in ``tlon.agents``. To keep the dependency
one-directional (agents → tools), the slice of the registry the tools need
is declared here as a ``Protocol``, together with the rejection errors both
sides share. The registry satisfies the protocol structurally; the assembler
(``assemble_member_capabilities`` in ``tlon.agents.assembly``) injects it.
"""

from typing import Any, Protocol

from tlon.base.config import AgentConfig
from tlon.organization.mail.resolver import MemberAddress


class SpawnRejectedError(ValueError):
    """Spawn refused by a budget or precondition check.

    ``reason`` is a machine-readable token: "max_activations" /
    "max_spawn_depth" / "session_quota" / "config_reload_failed" /
    "untrusted_prototype".
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class CapabilityProposalError(ValueError):
    """Capability staging-write rejection (parse / validation / name conflict).

    Raised by ``tlon.agents.capabilities`` (its ``CapabilityError``
    subclasses this) and caught by the capability tools; declared here so
    ``tlon.tools`` stays free of ``tlon.agents`` imports — the
    ``PrototypeError`` precedent (``tools/prototypes/ports.py``).
    """


class CapabilitySummary(Protocol):
    """Read-only view of a staged capability's metadata used by the tools.

    ``agents.capabilities.info.CapabilityInfo`` satisfies it structurally;
    the assembler injects ``write_staging_capability`` as a plain callable.
    """

    @property
    def name(self) -> str: ...

    @property
    def trusted(self) -> bool: ...


class StopRejectedError(ValueError):
    """Stop refused by a precondition check.

    ``reason`` is a machine-readable token: "unknown_agent" /
    "active_sessions".
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class MemberDirectory(Protocol):
    """Public member addresses from the delivery layer."""

    def list_members(self) -> list[MemberAddress]: ...


class AgentLifecycle(Protocol):
    """The slice of ``AgentRegistry`` the lifecycle tools depend on."""

    async def spawn_member(
        self,
        prototype: str,
        name: str,
        spawner_id: str,
        session_id: str | None,
        params: dict[str, Any] | None = None,
    ) -> AgentConfig: ...


class SelfStateReader(Protocol):
    """Read-only self scheduling status, bound by the assembler."""

    async def get_agent_status(self, agent_id: str) -> dict[str, Any] | None: ...
