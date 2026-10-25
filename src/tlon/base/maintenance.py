"""Retry pacing for failure notifications, never effect recovery."""

from time import monotonic

from tlon.base.execution import (
    BudgetExceededError,
    ExecutionDeadlineError,
    ModelRequestRejectedError,
    UnknownModelOutcomeError,
)


class MaintenanceRetry:
    """Bound transient retries; stop replaying receipts that cannot change.

    This process-local gate grants no new model allowance and owns no job
    progress. Rebuilding the member instance rechecks the original durable receipts.
    """

    def __init__(self) -> None:
        self.error: str | None = None
        self.blocked = False
        self._failures = 0
        self._retry_at = 0.0

    def ready(self) -> bool:
        return not self.blocked and monotonic() >= self._retry_at

    def succeeded(self) -> None:
        self.error = None
        self.blocked = False
        self._failures = 0
        self._retry_at = 0.0

    def failed(self, error: Exception, *, blocked: bool = False) -> None:
        self._failures = min(self._failures + 1, 5)
        self.blocked = blocked or isinstance(
            error,
            (
                BudgetExceededError,
                ExecutionDeadlineError,
                ModelRequestRejectedError,
                UnknownModelOutcomeError,
            ),
        )
        delay = min(30 * 2 ** (self._failures - 1), 300)
        self._retry_at = monotonic() + delay
        if isinstance(error, UnknownModelOutcomeError):
            status = "blocked; outcome unknown; manual reconciliation required; keep reservation and do not resend"
        elif self.blocked:
            status = "blocked; inspect saved receipt and rebuild member instance after repair"
        else:
            status = f"retry in {delay}s"
        self.error = f"{type(error).__name__}: {error} ({status})"
