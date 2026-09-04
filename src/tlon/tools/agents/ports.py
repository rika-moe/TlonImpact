"""Ports for the agent lifecycle tools (dependency inversion, ).

The spawn/stop/list tools live in ``tlon.tools`` while their implementation
(``AgentRegistry``) lives in ``tlon.agents``. To keep the dependency
one-directional (agents → tools), the slice of the registry the tools need
is declared here as a ``Protocol``, together with the rejection errors both
sides share. The registry satisfies the protocol structurally; the assembler
(``assemble_cognitive`` in ``tlon.agents.assembly``) injects it.
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
    "active_sessions".
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


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

    async def stop_member(self, agent_id: str, stopped_by: str, when_idle: bool = False) -> bool:
        """Stop a member. Returns True when it was stopped now; False only
        when ``when_idle`` registered a pending stop instead (a member with
        live sessions). Raises ``StopRejectedError`` otherwise."""
        ...

    def get_all_agent_status(self) -> list[dict[str, Any]]: ...
