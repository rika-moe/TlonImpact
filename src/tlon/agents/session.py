"""AgentSession — one active conversation / task context."""

import asyncio
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tlon.organization.mail.mail import Mail


class SessionState(Enum):
    ACTIVE = "active"
    WAITING = "waiting"
    COMPLETED = "completed"


class AgentSession:
    """A single conversation or task that the agent is working on."""

    def __init__(
        self,
        session_id: str,
        trigger_content: str,
        carrier: dict[str, str | None] | None = None,
        responsibility: "list[Mail] | None" = None,
        recipe_source: str = "",
        source_mail_id: str | None = None,
        invocation: dict[str, Any] | None = None,
    ):
        self.recipe_source = recipe_source
        self.source_mail_id = source_mail_id
        self.invocation = invocation or {}
        self.id = session_id
        self.context: list[dict[str, Any]] = []
        # Length of the initial context block (knowledge + mail history +
        # trigger text, journaled with session/start): the compaction
        # anchor — messages before it are never compressed (ADR 0008).
        # Restored from the journal on resume; 0 until the first build.
        self.anchor_len = 0
        self.created_at = datetime.now(UTC)
        self.last_active_at = datetime.now(UTC)
        self.state = SessionState.ACTIVE
        self.trigger_content = trigger_content

        # Responsibility set (ADR 0028): the wake-time snapshot of the
        # trigger mails this session is accountable for. In-memory
        # only — the journal persists the id list (session/start), and the
        # failure-reply anchors read these Mail objects.
        self.responsibility: list[Mail] = list(responsibility or [])

        # Trace carrier from the routing event (trace_id/span_id);
        # re-attached each _main_loop round since OTel context is contextvar-based
        self.carrier = carrier

        # ReAct loop state
        self._iteration = 0
        self._error_count = 0
        # Session-level cumulative token budget consumption (ADR 0023):
        # a projection of the shared total budget, including nested model calls;
        # compaction shares it; post-session distillation uses a maintenance root.
        self._token_usage = 0
        self._last_llm_response: Any = None
        self._all_tool_results: list[dict[str, Any]] = []

        # Per-session scratchpad shared between loop stages (phase 7c):
        # plan text, critique verdicts, etc. Owned by the engine; the DSL
        # can only read it through stage-condition predicates.
        self.loop_scratch: dict[str, Any] = {}

        # Background tool execution tracking
        self._pending_tasks: dict[str, asyncio.Task[Any]] = {}
        self._pending_tool_calls: list[Any] = []

    @property
    def is_completed(self) -> bool:
        return self.state == SessionState.COMPLETED

    @property
    def iteration(self) -> int:
        return self._iteration

    @property
    def error_count(self) -> int:
        """Consecutive think-stage failures since the last successful think.

        Not a lifetime total: ``reset_errors`` (declared by think on
        success, applied by the engine) zeroes it, so
        ``max_consecutive_errors`` bounds error *streaks* only.
        """
        return self._error_count

    @property
    def last_llm_response(self) -> Any:
        return self._last_llm_response

    @property
    def all_tool_results(self) -> list[dict[str, Any]]:
        return self._all_tool_results

    def mark_waiting(self) -> None:
        self.state = SessionState.WAITING
        self.last_active_at = datetime.now(UTC)

    def mark_active(self) -> None:
        self.state = SessionState.ACTIVE
        self.last_active_at = datetime.now(UTC)

    def mark_completed(self) -> None:
        self.state = SessionState.COMPLETED

    def advance_iteration(self) -> None:
        self._iteration += 1

    def record_error(self) -> None:
        """Record one consecutive error (see the error_count property)."""
        self._error_count += 1

    def reset_errors(self) -> None:
        """Zero the consecutive error count (a think call succeeded)."""
        self._error_count = 0

    @property
    def token_usage(self) -> int:
        """Cumulative tokens spent by this session's stage LLM calls."""
        return self._token_usage

    def add_tokens(self, n: int) -> None:
        """Add one stage LLM call's token consumption to the session total."""
        self._token_usage += n

    def set_last_llm_response(self, response: Any) -> None:
        self._last_llm_response = response

    def add_tool_result(self, tool_call: Any, result: Any) -> None:
        self._all_tool_results.append({"tool_call": tool_call, "result": result})

    def dispatch_tools(self, tool_calls: list[Any], tasks: dict[str, asyncio.Task[Any]]) -> None:
        """Record dispatched tool tasks."""
        self._pending_tasks = tasks
        self._pending_tool_calls = tool_calls

    async def cancel_pending(self) -> None:
        """Cancel all unfinished background tool tasks.

        Called when the session ends (normal completion or error) so no
        orphaned tasks keep running after the session is gone.
        """
        pending: list[asyncio.Task[Any]] = [
            t for t in self._pending_tasks.values() if t is not None and not t.done()
        ]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._pending_tasks = {}
        self._pending_tool_calls = []

    def collect_completed(self) -> list[tuple[Any, Any]]:
        """Return list of (tool_call, result) for completed tasks."""
        completed = []
        still_pending = {}

        for tc in self._pending_tool_calls:
            task = self._pending_tasks.get(tc.id)
            if task and task.done():
                # _run_one returns (tc, result); extract the result
                raw = task.result()
                result = raw[1] if isinstance(raw, tuple) and len(raw) == 2 else raw
                completed.append((tc, result))
            else:
                if task:
                    still_pending[tc.id] = task

        self._pending_tasks = still_pending
        self._pending_tool_calls = [
            tc
            for tc in self._pending_tool_calls
            if not (self._pending_tasks.get(tc.id) and self._pending_tasks[tc.id].done())
        ]
        return completed

    @property
    def has_pending_tools(self) -> bool:
        return len(self._pending_tasks) > 0

    def get_pending_tasks(self) -> dict[str, asyncio.Task[Any]]:
        """Return current pending tool tasks (for test synchronization)."""
        return dict(self._pending_tasks)
