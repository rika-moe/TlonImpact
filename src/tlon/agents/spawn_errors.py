"""Spawn/stop rejection errors (stage 6b of docs/design/dsl-agents.md).

Kept in their own module so both AgentRegistry (which raises them) and the
spawn/stop tools (which catch them) can import without a cycle — the
registry imports the tools at module level for assembly.
"""


class SpawnRejectedError(ValueError):
    """Spawn refused by a budget or precondition check.

    ``reason`` is a machine-readable token: "max_activations" /
    "max_spawn_depth" / "session_quota" / "config_reload_failed" /
    "untrusted_prototype".
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class StopRejectedError(ValueError):
    """Stop refused by a precondition check.

    ``reason`` is a machine-readable token: "unknown_agent" /
    "resident_not_stoppable" / "active_sessions" / "invalid_agent_id".
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
