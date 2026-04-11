"""Tool execution over EventBus (Phase 3).

Provides async RPC mechanism for tool execution via pub-sub pattern.
Supports correlation_id matching, timeouts, and cross-process communication.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

from tlon.tools import ToolResult


@dataclass
class ToolExecuteRequest:
    """Tool execution request payload.

    Attributes:
        correlation_id: Unique ID to match response with request
        agent_id: ID of the requesting agent
        tool_name: Name of the tool to execute
        arguments: Tool arguments
        timeout_seconds: Maximum time to wait for execution
    """

    correlation_id: str
    agent_id: str
    tool_name: str
    arguments: dict[str, Any]
    timeout_seconds: float = 60.0


@dataclass
class ToolExecuteResult:
    """Tool execution result payload.

    Attributes:
        correlation_id: Matches the request's correlation_id
        agent_id: ID of the requesting agent (for routing)
        success: Whether execution succeeded
        result: ToolResult data (if success)
        error: Error message (if not success)
    """

    correlation_id: str
    agent_id: str
    success: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class ToolExecutionError(Exception):
    """Raised when tool execution fails."""

    pass


class ToolTimeoutError(ToolExecutionError):
    """Raised when tool execution times out."""

    def __init__(self, correlation_ids: list[str], timeout: float):
        self.correlation_ids = correlation_ids
        self.timeout = timeout
        super().__init__(f"Tools timed out after {timeout}s: {correlation_ids}")


class ToolExecutionManager:
    """Manages pending tool execution futures for an Agent.

    Handles correlation_id matching and timeout cleanup.
    Thread-safe for asyncio context.
    """

    def __init__(self, default_timeout: float = 60.0, max_stale_ids: int = 1000):
        self._pending: dict[str, asyncio.Future[ToolResult]] = {}
        self._default_timeout = default_timeout
        self._max_stale_ids = max_stale_ids
        # Use dict to maintain insertion order (Python 3.7+)
        self._completed_correlation_ids: dict[str, None] = {}

    def create_future(self, correlation_id: str) -> asyncio.Future[ToolResult]:
        """Create a future for a pending tool execution.

        Args:
            correlation_id: Unique ID for this execution

        Returns:
            Future that will be resolved when result arrives
        """
        future: asyncio.Future[ToolResult] = asyncio.get_event_loop().create_future()
        self._pending[correlation_id] = future
        return future

    def resolve(self, correlation_id: str, result: ToolResult) -> bool:
        """Resolve a pending future with a result.

        Args:
            correlation_id: ID of the execution
            result: Tool execution result

        Returns:
            True if future was found and resolved, False otherwise
        """
        future = self._pending.pop(correlation_id, None)
        if future and not future.done():
            future.set_result(result)
            self._completed_correlation_ids[correlation_id] = None
            self._maybe_clear_old_stale_ids()
            return True
        return False

    def reject(self, correlation_id: str, error: Exception) -> bool:
        """Reject a pending future with an error.

        Args:
            correlation_id: ID of the execution
            error: Exception to set

        Returns:
            True if future was found and rejected, False otherwise
        """
        future = self._pending.pop(correlation_id, None)
        if future and not future.done():
            future.set_exception(error)
            return True
        return False

    def cleanup(self, correlation_id: str) -> None:
        """Clean up a pending future (e.g., on timeout).

        Args:
            correlation_id: ID to clean up
        """
        self._pending.pop(correlation_id, None)

    def is_stale_result(self, correlation_id: str) -> bool:
        """Check if a correlation_id is for a completed/stale result.

        Used to detect and ignore late-arriving responses.

        Args:
            correlation_id: ID to check

        Returns:
            True if this ID was already completed
        """
        return correlation_id in self._completed_correlation_ids

    def _maybe_clear_old_stale_ids(self) -> None:
        """Clear oldest stale IDs if exceeding max size.

        Maintains insertion order using dict (LRU behavior).
        """
        if len(self._completed_correlation_ids) > self._max_stale_ids:
            # Remove oldest entries (first items in ordered dict)
            to_remove = len(self._completed_correlation_ids) - self._max_stale_ids
            for _ in range(to_remove):
                if self._completed_correlation_ids:
                    self._completed_correlation_ids.pop(next(iter(self._completed_correlation_ids)))

    def mark_stale(self, correlation_id: str) -> None:
        """Mark a correlation_id as stale/completed.

        Args:
            correlation_id: ID to mark
        """
        self._completed_correlation_ids[correlation_id] = None
        self._maybe_clear_old_stale_ids()

    @property
    def pending_count(self) -> int:
        """Number of pending executions."""
        return len(self._pending)

    def get_pending_ids(self) -> set[str]:
        """Get set of pending correlation IDs."""
        return set(self._pending.keys())
