"""StageOutcome — a stage's declared intent, executed by the engine (C1).

Stages are pure transformations ``Session x Services -> StageOutcome``:
every system-state change a stage wants (context appends, scratch
writes, iteration/error counters, session-field writes such as the
last LLM response / tool-result log / ACTIVE transition, and — from
C2 — tool dispatches and the completion signal)
is returned as data; the LoopDriver applies it. Read-only external
queries (the LLM call) and observability (logs/events/traces) go
through the injected :mod:`tlon.agents.stages.services` instead — they
are not state changes and never appear here.

The dataclasses are mutable: the containers (lists/dicts) are built up
incrementally by the stages that fill them, and freezing the shell
while the containers stay mutable would buy no real immutability.
"""

from dataclasses import dataclass, field
from typing import Any

from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.tools.base import ToolResult


@dataclass
class StageOutcome:
    """Everything a stage wants changed, as data.

    Defaults are all empty/False so a stage only declares what it
    actually changes; the engine's application of an all-default
    outcome is a no-op.
    """

    response: LLMResponse | None = None
    """This round's LLM response; non-None updates the response chain."""

    context_appends: list[dict[str, Any]] = field(default_factory=list)
    """Messages to append to ``session.context``."""

    scratch_updates: dict[str, Any] = field(default_factory=dict)
    """Entries to write into ``session.loop_scratch``."""

    advance_iteration: bool = False
    """Advance the session's iteration counter."""

    record_error: bool = False
    """Record one error against the session."""

    clear_errors: bool = False
    """Reset the session's consecutive error count to zero (think succeeded)."""

    token_usage: int = 0
    """Tokens consumed by this stage's LLM call (session budget, ADR 0023).

    Zero = nothing to declare (no call happened, or the call failed
    before producing a response)."""

    tool_results: list[tuple[ToolCall, ToolResult]] = field(default_factory=list)
    """Harvested tool results to record on the session (C2)."""

    mark_active: bool = False
    """Transition the session back to ACTIVE (WAITING -> ACTIVE, C2)."""

    tool_dispatches: list[ToolCall] = field(default_factory=list)
    """Tool calls to dispatch as background tasks (C2)."""

    complete: bool = False
    """Mark the session completed (C2)."""
