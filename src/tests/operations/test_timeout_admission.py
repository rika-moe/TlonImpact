"""Timeout admission belongs to operations, independently of the removed stages."""

from pathlib import Path
from typing import Any

import pytest

from tlon.agents.program_store import ProgramStore
from tlon.base.execution import BudgetExceededError, ExecutionDeadlineError, ExecutionScope
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.schema import DefinitionError


async def _handler(context: CallContext, arguments: dict[str, Any]) -> int:
    return 7


def _operation(
    *, timeout: Any = None, grace: Any = 0, named: bool = False, handler: Any = _handler
) -> Operation:
    return Operation(
        "sample",
        "timeout test",
        {
            "type": "object",
            "properties": {"timeout": {"type": "number", "minimum": 0.001}} if named else {},
            "required": [],
            "additionalProperties": False,
        },
        {"type": "integer"},
        handler,
        effects=frozenset({"write"}),
        default_timeout=timeout,
        timeout_grace=grace,
    )


@pytest.mark.parametrize(
    "value", [True, False, float("nan"), float("inf"), float("-inf"), -1, 0, "1"]
)
def test_operation_rejects_invalid_default_timeout(value: Any) -> None:
    with pytest.raises(DefinitionError, match="default_timeout"):
        _operation(timeout=value)


@pytest.mark.parametrize(
    "value", [True, False, None, float("nan"), float("inf"), float("-inf"), -1, "1"]
)
def test_operation_rejects_invalid_timeout_grace(value: Any) -> None:
    with pytest.raises(DefinitionError, match="timeout_grace"):
        _operation(grace=value)


@pytest.mark.parametrize(
    "value", [True, False, None, float("nan"), float("inf"), float("-inf"), -1, 0, "1"]
)
def test_executor_rejects_invalid_default_timeout(value: Any) -> None:
    with pytest.raises(DefinitionError, match="executor default_timeout"):
        OperationExecutor(
            OperationRegistry([_operation()]),
            CallContext("a", "root", "call"),
            default_timeout=value,
        )


@pytest.mark.parametrize(
    "operation_timeout,named,arguments,grace,expected",
    [
        (None, False, {}, 0, 10),
        (20, False, {}, 0, 20),
        (20, True, {"timeout": 30}, 0, 30),
        (20, True, {}, 0, 20),
        (20, False, {"timeout": 30}, 0, 20),
        (20, True, {"timeout": 30}, 5, 35),
        (None, False, {}, 5, 15),
    ],
)
def test_timeout_precedence_only_accepts_schema_declared_override(
    operation_timeout: float | None,
    named: bool,
    arguments: dict[str, Any],
    grace: float,
    expected: float,
) -> None:
    operation = _operation(timeout=operation_timeout, grace=grace, named=named)
    executor = OperationExecutor(
        OperationRegistry([operation]), CallContext("a", "root", "call"), default_timeout=10
    )
    assert executor._timeout(operation, arguments, None) == expected


@pytest.mark.parametrize("remaining,expected", [(12, 12), (40, 35)])
def test_root_deadline_caps_the_selected_timeout_after_grace(
    monkeypatch: pytest.MonkeyPatch,
    remaining: float,
    expected: float,
) -> None:
    monkeypatch.setattr("tlon.base.execution.time.time", lambda: 100.0)
    store = ProgramStore(None, build_id="timeout")
    root = ExecutionScope(store, "root", 1000, "output", deadline=100 + remaining)
    # A longer child deadline cannot extend the caller's remaining time.
    child = root.child("child", deadline=200)
    operation = _operation(timeout=20, grace=5, named=True)
    executor = OperationExecutor(
        OperationRegistry([operation]), CallContext("a", "root", "call"), default_timeout=10
    )
    assert executor._timeout(operation, {"timeout": 30}, child) == expected


@pytest.mark.parametrize("disk", [False, True], ids=["memory", "sqlite"])
@pytest.mark.parametrize("failure", [ExecutionDeadlineError, BudgetExceededError])
async def test_committed_success_wins_over_later_host_control_failure(
    tmp_path: Path,
    disk: bool,
    failure: type[Exception],
) -> None:
    store = ProgramStore(tmp_path / "program.sqlite3" if disk else None, build_id="timeout")
    calls = 0

    async def committed(context: CallContext, arguments: dict[str, Any]) -> int:
        nonlocal calls
        calls += 1
        assert context.invocation is not None
        store.atomic_state_put(context.invocation, "committed", 7)
        raise failure("later host control check")

    operation = _operation(handler=committed)
    executor = OperationExecutor(
        OperationRegistry([operation]), CallContext("a", "root", "call"), store
    )
    assert await executor.call("sample", {}) == 7
    assert await executor.call("sample", {}) == 7
    assert store.state_get("committed") == 7
    assert calls == 1
