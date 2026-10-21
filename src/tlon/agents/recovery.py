"""Classify roots whose original effects cannot safely be resumed."""

from tlon.base.execution import UnknownModelOutcomeError
from tlon.operations.schema import RecoveryMismatchError, ReplayConflictError, UnknownOutcomeError


class RecoveryBlockedError(RecoveryMismatchError):
    """A durably quarantined root must never resume its old continuation."""


def blocks_recovery(error: BaseException) -> bool:
    """Identity mismatches and unsafe unknown effects cannot resume automatically."""
    seen: set[int] = set()
    while id(error) not in seen:
        seen.add(id(error))
        if isinstance(
            error, (RecoveryMismatchError, ReplayConflictError, UnknownModelOutcomeError)
        ):
            return True
        if isinstance(error, UnknownOutcomeError) and error.data.get("retry_safe") is False:
            return True
        if error.__cause__ is None:
            break
        error = error.__cause__
    return False


def blocked_reason(error: BaseException) -> str:
    return f"recovery_blocked:{type(error).__name__}: {error}"
