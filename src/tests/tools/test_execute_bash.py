"""Shell domain retains per-member tasks and projects structured failures."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from tlon.base.config import DockerRuntimeConfig
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.schema import OperationError
from tlon.operations.shell import make_shell_operations
from tlon.sandbox.device import ExecutionResult
from tlon.sandbox.shell import MemberShellService


def service_and_tools(device=None, level="L2"):
    service = MemberShellService(DockerRuntimeConfig(level=level), device)
    executor = OperationExecutor(
        OperationRegistry(make_shell_operations(service)), CallContext("a", "r", "c")
    )
    return service, OperationTool(executor, "shell-run"), OperationTool(executor, "shell-check")


async def test_task_isolation_shutdown_and_cancellation():
    gate = asyncio.Event()
    device = AsyncMock()
    device.run.side_effect = lambda *a, **k: gate.wait()

    # A real coroutine owns cancellation while waiting for the device.
    async def run(*args, **kwargs):
        await gate.wait()
        return ExecutionResult(0, "done", "")

    device.run.side_effect = run
    service, run_tool, check = service_and_tools(device)
    other, _, other_check = service_and_tools(device)
    started = await run_tool.execute_context({"command": "sleep", "background": True})
    task_id = started.data["task_id"]
    assert (await check.execute_context({"task_id": task_id})).data["status"] == "running"
    assert not (await other_check.execute_context({"task_id": task_id})).success
    task = service._tasks[task_id]
    await service.shutdown()
    assert task.cancelled()
    assert not (await check.execute_context({"task_id": task_id})).success
    assert not other._tasks


async def test_finished_output_retained_and_nonzero_is_failure():
    device = AsyncMock()
    device.run.return_value = ExecutionResult(3, "partial", "boom")
    service, run, check = service_and_tools(device)
    foreground = await run.execute_context({"command": "false"})
    assert not foreground.success
    assert foreground.data["stdout"] == "partial"
    started = await run.execute_context({"command": "false", "background": True})
    await service._tasks[started.data["task_id"]]
    for _ in range(2):
        result = await check.execute_context({"task_id": started.data["task_id"]})
        assert not result.success
        assert result.data["exit_code"] == 3
        assert result.data["stderr"] == "boom"
    await service.shutdown()


async def test_running_quota_and_finished_retention():
    gate = asyncio.Event()
    device = AsyncMock()

    async def run(*args, **kwargs):
        await gate.wait()
        return ExecutionResult(0, "ok", "")

    device.run.side_effect = run
    service, tool, _ = service_and_tools(device)
    for _ in range(20):
        assert (await tool.execute_context({"command": "wait", "background": True})).success
    assert not (await tool.execute_context({"command": "overflow", "background": True})).success
    gate.set()
    await asyncio.gather(*service._tasks.values())
    for _ in range(55):
        result = await tool.execute_context({"command": "done", "background": True})
        await service._tasks[result.data["task_id"]]
        await asyncio.sleep(0)
    assert len(service._tasks) == 50
    await service.shutdown()


@pytest.mark.parametrize("level,device", [("L0", AsyncMock()), ("L1", AsyncMock()), ("L2", None)])
async def test_level_and_unavailable_device_rejected(level, device):
    _, run, _ = service_and_tools(device, level)
    if device is None:
        with pytest.raises(OperationError, match="unavailable"):
            await run.execute_context({"command": "true"})
    else:
        assert not (await run.execute_context({"command": "true"})).success
    if device is not None:
        device.run.assert_not_called()


@pytest.mark.parametrize("timeout", [0, 301, True, "60"])
async def test_timeout_schema_is_shared_boundary(timeout):
    _, run, _ = service_and_tools(AsyncMock())
    result = await run.execute_context({"command": "true", "timeout": timeout})
    assert not result.success
    assert "InputError" in result.error
