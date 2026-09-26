"""Tests for ExecuteBashTool / CheckBashTaskTool background task isolation.

Regression coverage for the shared module-level task table: each tool
instance (one per agent) must own its table and quota, checkBashTask must
not see other instances' tasks, and shutdown must cancel pending tasks.
Finished tasks keep their results queryable for a bounded retention window
(``_MAX_FINISHED_TASKS`` per agent) instead of vanishing on completion.
"""

import asyncio

import pytest

import tlon.tools.shell.execute_bash as execute_bash_module
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.config import DockerRuntimeConfig
from tlon.sandbox.device import ExecutionResult
from tlon.tools.shell.execute_bash import CheckBashTaskTool, ExecuteBashTool


class _GatedRuntime:
    """Duck-typed ExecutionDevice: run() blocks until released."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()

    async def run(self, program: str, timeout: int = 60) -> ExecutionResult:
        self.started.set()
        await self.release.wait()
        return ExecutionResult(exit_code=0, stdout=program, stderr="")


class _StaticRuntime:
    """Duck-typed ExecutionDevice: run() completes immediately."""

    def __init__(self, exit_code: int = 0, stdout: str = "", stderr: str = "") -> None:
        self._exit_code = exit_code
        self._stdout = stdout
        self._stderr = stderr

    async def run(self, program: str, timeout: int = 60) -> ExecutionResult:
        return ExecutionResult(exit_code=self._exit_code, stdout=self._stdout, stderr=self._stderr)


def _make_tool(runtime: _GatedRuntime | _StaticRuntime) -> ExecuteBashTool:
    return ExecuteBashTool(DockerRuntimeConfig(), runtime)  # type: ignore[arg-type]


async def test_task_tables_are_isolated_between_instances():
    runtime_a = _GatedRuntime()
    runtime_b = _GatedRuntime()
    tool_a = _make_tool(runtime_a)
    tool_b = _make_tool(runtime_b)

    started = await tool_a.execute(command="sleep 60", background=True)
    assert started.success
    task_id = started.data["task_id"]

    # A sees its own task; B's table is untouched and cannot see A's task.
    assert task_id in tool_a.background_tasks
    assert tool_b.background_tasks == {}

    check_b = CheckBashTaskTool(tool_b.background_tasks)
    not_found = await check_b.execute(task_id=task_id)
    assert not not_found.success
    assert "not found" in not_found.error

    check_a = CheckBashTaskTool(tool_a.background_tasks)
    running = await check_a.execute(task_id=task_id)
    assert running.success
    assert running.data["status"] == "running"

    await tool_a.shutdown()
    await tool_b.shutdown()


async def test_quota_is_per_instance(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(execute_bash_module, "_MAX_BACKGROUND_TASKS", 2)
    tool_a = _make_tool(_GatedRuntime())
    tool_b = _make_tool(_GatedRuntime())

    assert (await tool_a.execute(command="a1", background=True)).success
    assert (await tool_a.execute(command="a2", background=True)).success
    rejected = await tool_a.execute(command="a3", background=True)
    assert not rejected.success
    assert "limit" in rejected.error

    # B's quota is unaffected by A being full.
    assert (await tool_b.execute(command="b1", background=True)).success

    await tool_a.shutdown()
    await tool_b.shutdown()


async def test_completed_task_result_is_retained_and_queryable():
    runtime = _GatedRuntime()
    tool = _make_tool(runtime)
    check = CheckBashTaskTool(tool.background_tasks)

    started = await tool.execute(command="echo hi", background=True)
    task_id = started.data["task_id"]
    task = tool.background_tasks[task_id]
    runtime.release.set()
    await task
    await asyncio.sleep(0)

    # The finished task stays in the table and its real result is queryable.
    assert task_id in tool.background_tasks
    result = await check.execute(task_id=task_id)
    assert result.success
    assert result.data["status"] == "completed"
    assert result.data["exit_code"] == 0
    assert result.data["stdout"] == "echo hi"

    # Querying is idempotent — the result survives repeated checks.
    again = await check.execute(task_id=task_id)
    assert again.data["stdout"] == "echo hi"

    await tool.shutdown()


async def test_failed_exit_code_result_is_retained():
    tool = _make_tool(_StaticRuntime(exit_code=3, stdout="partial", stderr="boom"))
    check = CheckBashTaskTool(tool.background_tasks)

    started = await tool.execute(command="false", background=True)
    task_id = started.data["task_id"]
    await tool.background_tasks[task_id]

    result = await check.execute(task_id=task_id)
    assert not result.success
    assert result.data["status"] == "completed"
    assert result.data["exit_code"] == 3
    assert result.data["stdout"] == "partial"
    assert result.data["stderr"] == "boom"
    assert result.error == "boom"

    await tool.shutdown()


async def test_finished_tasks_are_evicted_beyond_retention_window(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(execute_bash_module, "_MAX_FINISHED_TASKS", 2)
    tool = _make_tool(_StaticRuntime(stdout="out"))
    check = CheckBashTaskTool(tool.background_tasks)

    task_ids = []
    for i in range(5):
        started = await tool.execute(command=f"cmd{i}", background=True)
        task_id = started.data["task_id"]
        await tool.background_tasks[task_id]
        task_ids.append(task_id)

    # The two oldest finished results were evicted; the recent ones remain.
    assert task_ids[0] not in tool.background_tasks
    assert task_ids[1] not in tool.background_tasks
    evicted = await check.execute(task_id=task_ids[0])
    assert not evicted.success
    assert "not found" in evicted.error

    for task_id in task_ids[2:]:
        retained = await check.execute(task_id=task_id)
        assert retained.data["status"] == "completed"
        assert retained.data["stdout"] == "out"

    await tool.shutdown()


async def test_finished_results_do_not_consume_running_quota(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(execute_bash_module, "_MAX_BACKGROUND_TASKS", 2)
    tool = _make_tool(_StaticRuntime())

    # Five tasks run to completion; none of them counts against the quota.
    for i in range(5):
        started = await tool.execute(command=f"cmd{i}", background=True)
        await tool.background_tasks[started.data["task_id"]]

    first = await tool.execute(command="live1", background=True)
    second = await tool.execute(command="live2", background=True)
    assert first.success
    assert second.success
    rejected = await tool.execute(command="live3", background=True)
    assert not rejected.success
    assert "limit" in rejected.error

    await tool.shutdown()


async def test_cancelled_task_reports_cancelled_status():
    runtime = _GatedRuntime()
    tool = _make_tool(runtime)
    check = CheckBashTaskTool(tool.background_tasks)

    started = await tool.execute(command="sleep 60", background=True)
    task_id = started.data["task_id"]
    tool.background_tasks[task_id].cancel()
    await asyncio.sleep(0)

    result = await check.execute(task_id=task_id)
    assert not result.success
    assert result.data["status"] == "cancelled"

    await tool.shutdown()


async def test_not_found_error_names_possible_causes():
    tool = _make_tool(_StaticRuntime())
    check = CheckBashTaskTool(tool.background_tasks)

    result = await check.execute(task_id="bg-doesnotexist")
    assert not result.success
    assert "not found" in result.error
    assert "retention window" in result.error
    assert "restarted" in result.error


async def test_shutdown_cancels_pending_tasks_and_clears_table():
    runtime = _GatedRuntime()
    tool = _make_tool(runtime)
    check = CheckBashTaskTool(tool.background_tasks)

    first = await tool.execute(command="sleep 60", background=True)
    second = await tool.execute(command="sleep 60", background=True)
    task_ids = [first.data["task_id"], second.data["task_id"]]
    tasks = [tool.background_tasks[tid] for tid in task_ids]

    await tool.shutdown()

    assert all(task.cancelled() for task in tasks)
    assert tool.background_tasks == {}
    for task_id in task_ids:
        result = await check.execute(task_id=task_id)
        assert not result.success
        assert "not found" in result.error


async def test_timeout_above_schema_maximum_is_rejected_with_the_real_limit():
    """Issue : the schema is the timeout authority — a value above the
    declared maximum is refused up front, naming the real bound (300)."""
    executor = ToolExecutor()
    executor.register_tool(ExecuteBashTool(DockerRuntimeConfig(), None))

    result = await executor.execute(
        "executeBashCommand", {"command": "true", "timeout": 301}
    )

    assert not result.success
    assert "maximum 300" in result.error


async def test_timeout_below_schema_minimum_is_rejected_with_the_real_limit():
    executor = ToolExecutor()
    executor.register_tool(ExecuteBashTool(DockerRuntimeConfig(), None))

    result = await executor.execute(
        "executeBashCommand", {"command": "true", "timeout": 0}
    )

    assert not result.success
    assert "minimum 1" in result.error
