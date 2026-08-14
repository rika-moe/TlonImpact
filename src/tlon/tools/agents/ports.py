"""Ports for the agent lifecycle tools (dependency inversion, ).

The spawn/stop/list tools live in ``tlon.tools`` while their implementation
(``AgentRegistry``) lives in ``tlon.agents``. To keep the dependency
one-directional (agents → tools), the slice of the registry the tools need
is declared here as a ``Protocol``, together with the rejection errors both
sides share. The registry satisfies the protocol structurally; the assembler
(``AgentRegistry._build_cognitive``) injects itself.
"""

from typing import Any, Protocol

from tlon.base.config import AgentConfig


class SpawnRejectedError(ValueError):
    """Spawn refused by a budget or precondition check.

    ``reason`` is a machine-readable token: "max_activations" /
    "max_spawn_depth" / "session_quota" / "config_reload_failed" /
    "untrusted_prototype".
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class StopRejectedError(ValueError):
    """Stop refused by a precondition check.

    ``reason`` is a machine-readable token: "unknown_agent" /
    "resident_not_stoppable" / "active_sessions" / "invalid_agent_id".
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class AgentLifecycle(Protocol):
    """The slice of ``AgentRegistry`` the lifecycle tools depend on."""

    async def spawn_contractor(
        self,
        prototype: str,
        name: str,
        spawner_id: str,
        session_id: str | None,
        params: dict[str, Any] | None = None,
    ) -> AgentConfig: ...

    async def stop_contractor(self, agent_id: str, stopped_by: str) -> bool: ...

    def get_all_agent_status(self) -> list[dict[str, Any]]: ...
