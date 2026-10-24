"""Ports and rejection types for member lifecycle services."""

from typing import Any, Protocol

from tlon.base.config import AgentConfig
from tlon.organization.mail.resolver import MemberAddress


class SpawnRejectedError(ValueError):
    """Spawn refused by a budget or precondition check.

    ``reason`` is a machine-readable token: "max_activations" /
    "max_spawn_depth" / "run_quota" / "config_reload_failed" /
    "untrusted_prototype" / "invalid_run".
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class StopRejectedError(ValueError):
    """Stop refused by a precondition check.

    ``reason`` is a machine-readable token: "unknown_agent" /
    "active_work".
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
        run_id: str,
        params: dict[str, Any] | None = None,
    ) -> AgentConfig: ...


class SelfStateReader(Protocol):
    """Read-only self scheduling status, bound by the assembler."""

    async def get_agent_status(self, agent_id: str) -> dict[str, Any] | None: ...
