"""Task operation projection and domain transition regressions."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from tlon.base.paths import TlonPaths
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.schema import UnknownOutcomeError
from tlon.operations.tasks import make_task_operations
from tlon.tasks.manager import TaskManager


@pytest.fixture
def manager(tmp_path: Path) -> TaskManager:
    return TaskManager("test-agent", TlonPaths(home=tmp_path))


def tool(manager: TaskManager, action: str) -> OperationTool:
    executor = OperationExecutor(
        OperationRegistry(make_task_operations(manager)),
        CallContext(manager.agent_id, "run", "call"),
    )
    return OperationTool(executor, f"task-{action}")


@pytest.mark.parametrize(
    "action,required",
    [
        ("create", ["title"]),
        ("list", []),
        ("current", []),
        ("start", ["task_id"]),
        ("pause", ["task_id"]),
        ("complete", ["task_id"]),
        ("remove", ["task_id"]),
    ],
)
def test_metadata(manager: TaskManager, action: str, required: list[str]) -> None:
    value = tool(manager, action)
    definition = value.to_openai_definition()["function"]
    assert definition["name"] == f"task-{action}"
    assert definition["parameters"]["required"] == required
    assert definition["parameters"]["additionalProperties"] is False


async def test_lifecycle_preserves_all_fields_and_progress(manager: TaskManager) -> None:
    create = await tool(manager, "create").execute_context({"title": "任务", "description": "描述"})
    assert create.success and create.data["task"]["status"] == "todo"
    assert create.data["task"]["description"] == "描述"
    await tool(manager, "start").execute_context({"task_id": "t-001"})
    paused = await tool(manager, "pause").execute_context(
        {"task_id": "t-001", "reason": "保存进度"}
    )
    assert paused.success and paused.data["task"]["pause_count"] == 1
    assert "[Paused] 保存进度" in paused.data["task"]["description"]
    current = await tool(manager, "current").execute_context({})
    assert current.data["task"]["status"] == "paused"
    await tool(manager, "start").execute_context({"task_id": "t-001"})
    complete = await tool(manager, "complete").execute_context(
        {"task_id": "t-001", "result": "交付物"}
    )
    assert complete.success and complete.data["task"]["result"] == "交付物"
    assert complete.data["task"]["completed_at"]
    removed = await tool(manager, "remove").execute_context({"task_id": "t-001"})
    assert removed.success and manager.get_task("t-001") is None


async def test_auto_pause_and_defaults(manager: TaskManager) -> None:
    await tool(manager, "create").execute_context({"title": "A"})
    await tool(manager, "create").execute_context({"title": "B"})
    assert manager.get_task("t-001").description == ""
    await tool(manager, "start").execute_context({"task_id": "t-001"})
    second = await tool(manager, "start").execute_context({"task_id": "t-002"})
    assert second.data["auto_paused"]["id"] == "t-001"
    assert second.data["task"]["status"] == "doing"


@pytest.mark.parametrize("action", ["start", "pause", "complete", "remove"])
async def test_missing_task_is_rejected(manager: TaskManager, action: str) -> None:
    result = await tool(manager, action).execute_context({"task_id": "missing"})
    assert not result.success and "not found" in result.error
    assert result.data["outcome"] == "rejected"


@pytest.mark.parametrize(
    "action,status",
    [
        ("pause", "todo"),
        ("complete", "todo"),
        ("remove", "todo"),
        ("start", "done"),
        ("complete", "done"),
        ("pause", "done"),
        ("remove", "doing"),
    ],
)
async def test_wrong_status_is_rejected(manager: TaskManager, action: str, status: str) -> None:
    manager.create_task("A")
    if status in ("doing", "done"):
        manager.start_task("t-001")
    if status == "done":
        manager.complete_task("t-001")
    result = await tool(manager, action).execute_context({"task_id": "t-001"})
    assert not result.success and result.data["outcome"] == "rejected"


async def test_list_filter_and_empty_current(manager: TaskManager) -> None:
    assert (await tool(manager, "current").execute_context({})).data == {"task": None}
    assert (await tool(manager, "list").execute_context({})).data["stats"]["total"] == 0
    manager.create_task("A")
    manager.create_task("B")
    manager.start_task("t-001")
    listing = await tool(manager, "list").execute_context({"filter": "doing"})
    assert listing.data["stats"]["total"] == 2
    assert [task["id"] for task in listing.data["tasks"]] == ["t-001"]


@pytest.mark.parametrize(
    "action,method",
    [
        ("create", "create_task"),
        ("start", "start_task"),
        ("pause", "pause_task"),
        ("complete", "complete_task"),
        ("remove", "remove_task"),
    ],
)
async def test_write_failure_is_unknown(manager: TaskManager, action: str, method: str) -> None:
    manager.create_task("A")
    if action in ("pause", "complete", "remove"):
        manager.start_task("t-001")
    if action == "remove":
        manager.complete_task("t-001")
    setattr(manager, method, Mock(side_effect=OSError("disk full")))
    args = {"title": "A"} if action == "create" else {"task_id": "t-001"}
    with pytest.raises(UnknownOutcomeError, match="disk full") as failure:
        await tool(manager, action).execute_context({**args})
    assert failure.value.data == {"outcome": "unknown", "call_id": "call", "retry_safe": False}


@pytest.mark.parametrize("action,method", [("list", "list_tasks"), ("current", "get_current_task")])
async def test_read_failure_propagates(manager: TaskManager, action: str, method: str) -> None:
    setattr(manager, method, Mock(side_effect=RuntimeError("read failed")))
    with pytest.raises(RuntimeError, match="read failed"):
        await tool(manager, action).execute_context({})
