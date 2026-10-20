"""Real task storage and operation boundary isolation."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from tlon.base.paths import TlonPaths
from tlon.operations.adapters import DSLAdapter
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.schema import InputError, OperationError, UnknownOutcomeError
from tlon.operations.tasks import make_task_operations, task_operation_definitions
from tlon.tasks.manager import TaskManager
from tlosure.builtins import BuiltinCall
from tlosure.edn import Keyword


async def test_real_managers_persist_and_isolate_both_entries(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    a, b = TaskManager("a", paths), TaskManager("b", paths)
    executor = OperationExecutor(
        OperationRegistry(make_task_operations(a)), CallContext("a", "run", "call")
    )
    adapter = DSLAdapter(executor)
    call = BuiltinCall(
        "task-create", adapter.builtins()["task-create"], [{Keyword("title"): "private"}], {}
    )
    created = await adapter.handle(call)
    assert created[Keyword("task")][Keyword("title")] == "private"
    restored = TaskManager("a", paths)
    restored.load()
    assert restored.get_task("t-001").title == "private"
    b.load()
    assert b.list_tasks() == []
    with pytest.raises(InputError):
        await executor.call("task-list", {"agent_id": "b"})
    wrong = OperationExecutor(executor.registry, CallContext("b", "run", "call"))
    with pytest.raises(OperationError, match="another member"):
        await wrong.call("task-list", {})


async def test_disk_failure_is_not_success(tmp_path: Path) -> None:
    manager = TaskManager("a", TlonPaths(home=tmp_path))
    executor = OperationExecutor(
        OperationRegistry(make_task_operations(manager)), CallContext("a", "run", "call")
    )
    manager._write_lines = Mock(side_effect=OSError("disk full"))
    with pytest.raises(UnknownOutcomeError) as failure:
        await executor.call("task-create", {"title": "x"})
    assert failure.value.data["retry_safe"] is False
    assert failure.value.data["call_id"] == "call"


def test_static_contracts() -> None:
    assert len(task_operation_definitions()) == 7
