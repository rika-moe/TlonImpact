"""Member-owned shell service and its sole background task table."""

import asyncio
import uuid
from typing import Any

from tlon.base.config import DockerRuntimeConfig
from tlon.operations.schema import OperationError, UnknownOutcomeError
from tlon.sandbox.device import ExecutionDevice, ExecutionResult

DEFAULT_TIMEOUT = 60


class ShellExecutionError(OperationError):
    """Command completed unsuccessfully; effects may already have occurred."""

    def __init__(self, message: str, data: dict[str, Any]) -> None:
        super().__init__(message)
        self.data = data


class MemberShellService:
    def __init__(self, config: DockerRuntimeConfig, device: ExecutionDevice | None) -> None:
        self.config = config
        self.device = device
        self._tasks: dict[str, asyncio.Task[ExecutionResult]] = {}

    def _device(self) -> ExecutionDevice:
        if self.config.level != "L2":
            raise OperationError("shell requires execution level L2")
        if self.device is None:
            raise OperationError("Declared execution device is unavailable")
        return self.device

    @staticmethod
    def _result(result: ExecutionResult, task_id: str = "") -> dict[str, Any]:
        data = {
            "task_id": task_id,
            "status": "completed",
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
        if result.exit_code == -1:
            raise UnknownOutcomeError(
                "Command interrupted; effects may have occurred",
                {**data, "outcome": "unknown", "retry_safe": False},
            )
        if result.exit_code != 0:
            data["outcome"] = "failed"
            raise ShellExecutionError(
                f"Command failed with exit code {result.exit_code}: {result.stderr}", data
            )
        return data

    def _trim(self) -> None:
        finished = [key for key, task in self._tasks.items() if task.done()]
        for key in finished[:-50]:
            del self._tasks[key]

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        device = self._device()
        command = args["command"]
        timeout = args.get("timeout", DEFAULT_TIMEOUT)
        if not args.get("background", False):
            return self._result(await self._run(device, command, timeout))
        self._trim()
        if sum(not task.done() for task in self._tasks.values()) >= 20:
            raise OperationError("Background task limit (20) reached")
        task_id = f"bg-{uuid.uuid4().hex}"
        task = asyncio.create_task(self._run(device, command, timeout))
        self._tasks[task_id] = task
        task.add_done_callback(lambda done: self._finished(done))
        return {
            "task_id": task_id,
            "status": "running",
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    async def _run(self, device: ExecutionDevice, command: str, timeout: int) -> ExecutionResult:
        try:
            return await device.run(command, timeout=timeout)
        except Exception as error:
            raise UnknownOutcomeError(
                f"Command execution outcome unknown: {error}",
                {"outcome": "unknown", "retry_safe": False},
            ) from error

    def _finished(self, task: asyncio.Task[ExecutionResult]) -> None:
        if not task.cancelled():
            task.exception()  # Observe failures even if nobody polls this handle.
        self._trim()

    async def check(self, args: dict[str, Any]) -> dict[str, Any]:
        self._device()
        task_id = args["task_id"]
        task = self._tasks.get(task_id)
        if task is None:
            raise OperationError("Task not found in this member's retained in-memory results")
        if not task.done():
            return {
                "task_id": task_id,
                "status": "running",
                "exit_code": None,
                "stdout": "",
                "stderr": "",
            }
        if task.cancelled():
            raise UnknownOutcomeError(
                "Task cancelled; effects may have occurred",
                {
                    "task_id": task_id,
                    "status": "cancelled",
                    "outcome": "unknown",
                    "retry_safe": False,
                },
            )
        try:
            result = task.result()
        except UnknownOutcomeError as error:
            error.data["task_id"] = task_id
            raise
        except Exception as error:
            raise ShellExecutionError(
                f"Task failed: {error}", {"task_id": task_id, "status": "failed"}
            ) from error
        return self._result(result, task_id)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
