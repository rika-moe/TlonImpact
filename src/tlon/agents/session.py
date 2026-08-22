"""AgentSession — one active conversation / task context."""

import asyncio
from datetime import datetime
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

    def __init__(self, session_id: str, trigger_content: str, carrier: dict[str, str | None] | None = None):
        self.id = session_id
        self.context: list[dict[str, Any]] = []
        self.created_at = datetime.now()
        self.last_active_at = datetime.now()
        self.state = SessionState.ACTIVE
        self.trigger_content = trigger_content

        # The mail that opened this session, recorded on the first step;
        # on-complete action expressions read it as ``msg`` (phase 4b).
        self.trigger_message: Mail | None = None

        # Trace carrier from the routing event (trace_id/span_id);
        # re-attached each _main_loop round since OTel context is contextvar-based
        self.carrier = carrier

        # ReAct loop state
        self._iteration = 0
        self._error_count = 0
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
        return self._error_count

    @property
    def last_llm_response(self) -> Any:
        return self._last_llm_response

    @property
    def all_tool_results(self) -> list[dict[str, Any]]:
        return self._all_tool_results

    def mark_waiting(self) -> None:
        self.state = SessionState.WAITING
        self.last_active_at = datetime.now()

    def mark_active(self) -> None:
        self.state = SessionState.ACTIVE
        self.last_active_at = datetime.now()

    def mark_completed(self) -> None:
        self.state = SessionState.COMPLETED

    def advance_iteration(self) -> None:
        self._iteration += 1

    def record_error(self) -> None:
        self._error_count += 1

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
        self._pending_tool_calls = [tc for tc in self._pending_tool_calls if not (self._pending_tasks.get(tc.id) and self._pending_tasks[tc.id].done())]
        return completed

    @property
    def has_pending_tools(self) -> bool:
        return len(self._pending_tasks) > 0

    def get_pending_tasks(self) -> dict[str, asyncio.Task[Any]]:
        """Return current pending tool tasks (for test synchronization)."""
        return dict(self._pending_tasks)
