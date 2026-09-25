"""Tests for the task Tool wrapper classes (tlon.tools.tasks.*).

These test the thin Tool layer around TaskManager: argument forwarding,
ToolResult shaping, and metadata. TaskManager's own behavior is covered by
src/tests/tasks/test_manager.py and test_models.py and is not re-tested here.

Error-path assertions pin down the *current* behavior: each tool catches
exceptions itself and returns ``ToolResult(success=False, error="Failed to
...")``. Issue #3 will move that fallback into ToolExecutor and delete the
per-tool try/except; when #3 lands, the ``test_execute_*_error`` tests here
must be updated to expect the exception to propagate instead.
"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from tlon.base.paths import TlonPaths
from tlon.tools.tasks.complete_task import CompleteTaskTool
from tlon.tools.tasks.create_task import CreateTaskTool
from tlon.tools.tasks.get_current_task import GetCurrentTaskTool
from tlon.tools.tasks.list_tasks import ListTasksTool
from tlon.tools.tasks.manager import TaskManager
from tlon.tools.tasks.pause_task import PauseTaskTool
from tlon.tools.tasks.remove_task import RemoveTaskTool
from tlon.tools.tasks.start_task import StartTaskTool


@pytest.fixture
def manager(tmp_path: Path) -> TaskManager:
    return TaskManager(agent_id="test-agent", tlon_paths=TlonPaths(home=tmp_path))


class TestToolMetadata:
    """Each tool's schema must match what its execute() consumes."""

    @pytest.mark.parametrize(
        ("tool_cls", "name", "required"),
        [
            (CreateTaskTool, "createTask", ["title"]),
            (CompleteTaskTool, "completeTask", ["task_id"]),
            (GetCurrentTaskTool, "getCurrentTask", []),
            (ListTasksTool, "listTasks", []),
            (PauseTaskTool, "pauseTask", ["task_id"]),
            (RemoveTaskTool, "removeTask", ["task_id"]),
            (StartTaskTool, "startTask", ["task_id"]),
        ],
    )
    def test_name_and_required(
        self, manager: TaskManager, tool_cls: type, name: str, required: list[str]
    ):
        tool = tool_cls(manager)

        assert tool.name == name
        assert tool.parameters["type"] == "object"
        assert tool.parameters.get("required", []) == required

    def test_openai_definition(self, manager: TaskManager):
        definition = CreateTaskTool(manager).to_openai_definition()

        assert definition["type"] == "function"
        assert definition["function"]["name"] == "createTask"
        assert "title" in definition["function"]["parameters"]["properties"]


class TestCreateTaskTool:
    async def test_execute_creates_task(self, manager: TaskManager):
        tool = CreateTaskTool(manager)

        result = await tool.execute(title="设计登录 API", description="定义端点")

        assert result.success is True
        assert result.data == {
            "task_id": "t-001",
            "title": "设计登录 API",
            "status": "todo",
        }
        assert "设计登录 API" in result.message
        assert "t-001" in result.message

    async def test_description_defaults_to_empty(self, manager: TaskManager):
        tool = CreateTaskTool(manager)

        result = await tool.execute(title="任务")

        assert result.success is True
        assert manager.get_task("t-001").description == ""

    async def test_forwards_arguments_to_manager(self, manager: TaskManager):
        spy = Mock(wraps=manager.create_task)
        manager.create_task = spy
        tool = CreateTaskTool(manager)

        await tool.execute(title="标题", description="描述")

        spy.assert_called_once_with("标题", "描述")

    async def test_execute_error(self, manager: TaskManager):
        manager.create_task = Mock(side_effect=RuntimeError("disk full"))
        tool = CreateTaskTool(manager)

        result = await tool.execute(title="任务")

        assert result.success is False
        assert result.data == {}
        assert result.error == "Failed to create task: disk full"


class TestStartTaskTool:
    async def test_execute_starts_task(self, manager: TaskManager):
        manager.create_task("任务A")
        tool = StartTaskTool(manager)

        result = await tool.execute(task_id="t-001")

        assert result.success is True
        assert result.data == {
            "task_id": "t-001",
            "title": "任务A",
            "status": "doing",
        }
        assert "任务A" in result.message

    async def test_auto_pauses_current_task(self, manager: TaskManager):
        manager.create_task("任务A")
        manager.create_task("任务B")
        manager.start_task("t-001")
        tool = StartTaskTool(manager)

        result = await tool.execute(task_id="t-002")

        assert result.success is True
        assert result.data["task_id"] == "t-002"
        assert result.data["auto_paused"] == {"task_id": "t-001", "title": "任务A"}
        assert "自动暂停" in result.message

    async def test_task_not_found(self, manager: TaskManager):
        tool = StartTaskTool(manager)

        result = await tool.execute(task_id="t-999")

        assert result.success is False
        assert result.error == "Task not found: t-999"

    async def test_execute_error(self, manager: TaskManager):
        manager.start_task = Mock(side_effect=RuntimeError("boom"))
        tool = StartTaskTool(manager)

        result = await tool.execute(task_id="t-001")

        assert result.success is False
        assert result.error == "Failed to start task: boom"


class TestPauseTaskTool:
    async def test_execute_pauses_doing_task(self, manager: TaskManager):
        manager.create_task("任务A")
        manager.start_task("t-001")
        tool = PauseTaskTool(manager)

        result = await tool.execute(task_id="t-001", reason="去修紧急 bug")

        assert result.success is True
        assert result.data == {
            "task_id": "t-001",
            "title": "任务A",
            "status": "paused",
            "pause_count": 1,
        }
        assert "去修紧急 bug" in result.message

    async def test_reason_appended_to_description(self, manager: TaskManager):
        manager.create_task("任务A", "原始描述")
        manager.start_task("t-001")
        tool = PauseTaskTool(manager)

        await tool.execute(task_id="t-001", reason="进度 50%")

        assert "[Paused] 进度 50%" in manager.get_task("t-001").description

    async def test_task_not_found(self, manager: TaskManager):
        tool = PauseTaskTool(manager)

        result = await tool.execute(task_id="t-999")

        assert result.success is False
        assert result.error == "Task not found: t-999"

    async def test_cannot_pause_non_doing_task(self, manager: TaskManager):
        manager.create_task("任务A")
        tool = PauseTaskTool(manager)

        result = await tool.execute(task_id="t-001")

        assert result.success is False
        assert "Cannot pause task with status 'todo'" in result.error

    async def test_execute_error(self, manager: TaskManager):
        manager.create_task("任务A")
        manager.start_task("t-001")
        manager.pause_task = Mock(side_effect=RuntimeError("boom"))
        tool = PauseTaskTool(manager)

        result = await tool.execute(task_id="t-001")

        assert result.success is False
        assert result.error == "Failed to pause task: boom"


class TestCompleteTaskTool:
    async def test_execute_completes_doing_task(self, manager: TaskManager):
        manager.create_task("任务A")
        manager.start_task("t-001")
        tool = CompleteTaskTool(manager)

        result = await tool.execute(task_id="t-001", result="已完成实现")

        assert result.success is True
        assert result.data["task_id"] == "t-001"
        assert result.data["title"] == "任务A"
        assert result.data["status"] == "done"
        assert "duration_minutes" in result.data
        assert result.data["pause_count"] == 0
        assert "任务A" in result.message

    async def test_forwards_result_summary_to_manager(self, manager: TaskManager):
        manager.create_task("任务A")
        manager.start_task("t-001")
        spy = Mock(wraps=manager.complete_task)
        manager.complete_task = spy
        tool = CompleteTaskTool(manager)

        await tool.execute(task_id="t-001", result="摘要")

        spy.assert_called_once_with("t-001", "摘要")

    async def test_task_not_found(self, manager: TaskManager):
        tool = CompleteTaskTool(manager)

        result = await tool.execute(task_id="t-999")

        assert result.success is False
        assert result.error == "Task not found: t-999"

    async def test_cannot_complete_todo_task(self, manager: TaskManager):
        manager.create_task("任务A")
        tool = CompleteTaskTool(manager)

        result = await tool.execute(task_id="t-001")

        assert result.success is False
        assert "hasn't been started" in result.error

    async def test_cannot_complete_already_done_task(self, manager: TaskManager):
        manager.create_task("任务A")
        manager.start_task("t-001")
        manager.complete_task("t-001")
        tool = CompleteTaskTool(manager)

        result = await tool.execute(task_id="t-001")

        assert result.success is False
        assert result.error == "Task t-001 is already completed"

    async def test_execute_error(self, manager: TaskManager):
        manager.get_task = Mock(side_effect=RuntimeError("boom"))
        tool = CompleteTaskTool(manager)

        result = await tool.execute(task_id="t-001")

        assert result.success is False
        assert result.error == "Failed to complete task: boom"


class TestRemoveTaskTool:
    async def test_execute_removes_done_task(self, manager: TaskManager):
        manager.create_task("任务A")
        manager.start_task("t-001")
        manager.complete_task("t-001")
        tool = RemoveTaskTool(manager)

        result = await tool.execute(task_id="t-001")

        assert result.success is True
        assert result.data == {"task_id": "t-001", "title": "任务A"}
        assert manager.get_task("t-001") is None

    async def test_task_not_found(self, manager: TaskManager):
        tool = RemoveTaskTool(manager)

        result = await tool.execute(task_id="t-999")

        assert result.success is False
        assert result.error == "Task not found: t-999"

    async def test_cannot_remove_non_done_task(self, manager: TaskManager):
        manager.create_task("任务A")
        tool = RemoveTaskTool(manager)

        result = await tool.execute(task_id="t-001")

        assert result.success is False
        assert "Cannot remove task with status 'todo'" in result.error

    async def test_execute_error(self, manager: TaskManager):
        manager.get_task = Mock(side_effect=RuntimeError("boom"))
        tool = RemoveTaskTool(manager)

        result = await tool.execute(task_id="t-001")

        assert result.success is False
        assert result.error == "Failed to remove task: boom"


class TestGetCurrentTaskTool:
    async def test_no_current_task(self, manager: TaskManager):
        tool = GetCurrentTaskTool(manager)

        result = await tool.execute()

        assert result.success is True
        assert result.data == {"has_task": False}
        assert "没有进行中的任务" in result.message

    async def test_returns_doing_task(self, manager: TaskManager):
        manager.create_task("任务A", "描述")
        manager.start_task("t-001")
        tool = GetCurrentTaskTool(manager)

        result = await tool.execute()

        assert result.success is True
        assert result.data["has_task"] is True
        task = result.data["task"]
        assert task["id"] == "t-001"
        assert task["title"] == "任务A"
        assert task["description"] == "描述"
        assert task["status"] == "doing"
        assert task["started_at"]
        assert isinstance(task["elapsed_minutes"], int)
        assert task["pause_count"] == 0

    async def test_falls_back_to_paused_task(self, manager: TaskManager):
        manager.create_task("任务A")
        manager.start_task("t-001")
        manager.pause_task("t-001")
        tool = GetCurrentTaskTool(manager)

        result = await tool.execute()

        assert result.success is True
        assert result.data["task"]["status"] == "paused"

    async def test_execute_error(self, manager: TaskManager):
        manager.get_current_task = Mock(side_effect=RuntimeError("boom"))
        tool = GetCurrentTaskTool(manager)

        result = await tool.execute()

        assert result.success is False
        assert result.error == "Failed to get current task: boom"


class TestListTasksTool:
    async def test_empty_list(self, manager: TaskManager):
        tool = ListTasksTool(manager)

        result = await tool.execute()

        assert result.success is True
        assert result.data["total"] == 0
        assert result.data["tasks"] == []
        assert "0个" in result.message

    async def test_lists_tasks_with_stats(self, manager: TaskManager):
        manager.create_task("任务A")
        manager.create_task("任务B")
        manager.start_task("t-001")
        tool = ListTasksTool(manager)

        result = await tool.execute()

        assert result.success is True
        assert result.data["total"] == 2
        assert result.data["doing"] == 1
        assert result.data["todo"] == 1
        assert result.data["tasks"] == [
            {"id": "t-001", "title": "任务A", "status": "doing"},
            {"id": "t-002", "title": "任务B", "status": "todo"},
        ]

    @pytest.mark.parametrize(
        ("filter_value", "expected_status_filter"),
        [("all", None), ("doing", "doing"), ("todo", "todo")],
    )
    async def test_filter_forwarded_to_manager(
        self,
        manager: TaskManager,
        filter_value: str,
        expected_status_filter: str | None,
    ):
        list_spy = Mock(wraps=manager.list_tasks)
        stats_spy = Mock(wraps=manager.get_stats)
        manager.list_tasks = list_spy
        manager.get_stats = stats_spy
        tool = ListTasksTool(manager)

        await tool.execute(filter=filter_value)

        list_spy.assert_called_once_with(expected_status_filter)
        stats_spy.assert_called_once_with()

    async def test_filter_defaults_to_all(self, manager: TaskManager):
        list_spy = Mock(wraps=manager.list_tasks)
        manager.list_tasks = list_spy
        tool = ListTasksTool(manager)

        await tool.execute()

        list_spy.assert_called_once_with(None)

    async def test_execute_error(self, manager: TaskManager):
        manager.list_tasks = Mock(side_effect=RuntimeError("boom"))
        tool = ListTasksTool(manager)

        result = await tool.execute()

        assert result.success is False
        assert result.error == "Failed to list tasks: boom"
