"""Maintenance pacing cannot grant a fresh root or spin terminal receipts."""

from pathlib import Path

import pytest

from tlon.base.execution import (
    BudgetExceededError,
    ModelRequestRejectedError,
    UnknownModelOutcomeError,
)
from tlon.base.maintenance import MaintenanceCheckpointError, MaintenanceRetry


def test_transient_retries_back_off_and_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = 0.0
    monkeypatch.setattr("tlon.base.maintenance.monotonic", lambda: clock)
    retry = MaintenanceRetry()
    for delay in (30, 60, 120, 240, 300, 300):
        assert retry.ready()
        retry.failed(OSError("storage unavailable"))
        assert not retry.ready()
        clock += delay - 1
        assert not retry.ready()
        clock += 1
        assert retry.ready()
    retry.succeeded()
    assert retry.error is None
    retry.failed(OSError("new failure"))
    clock += 30
    assert retry.ready()


@pytest.mark.parametrize("error", [
    BudgetExceededError("spent"), ModelRequestRejectedError("rejected"),
    UnknownModelOutcomeError("unknown"), MaintenanceCheckpointError(Path("checkpoint.json"), "bad JSON"),
])
def test_terminal_receipts_do_not_retry(error: Exception, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = 0.0
    monkeypatch.setattr("tlon.base.maintenance.monotonic", lambda: clock)
    retry = MaintenanceRetry()
    retry.failed(error)
    clock += 10_000
    assert retry.blocked and not retry.ready()
    assert "blocked" in str(retry.error)
