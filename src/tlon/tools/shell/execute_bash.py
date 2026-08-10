"""Execute Bash command tool with background task support."""

import asyncio
import uuid
from typing import Any

from tlon.base.config import DockerRuntimeConfig
from tlon.base.logger import get_logger
from tlon.sandbox.docker_runtime import DockerRuntime, ExecutionResult
from tlon.tools.base import Tool, ToolResult

logger = get_logger(__name__)

_background_tasks: dict[str, asyncio.Task[ExecutionResult]] = {}
_MAX_BACKGROUND_TASKS = 20


class ExecuteBashTool(Tool):
    """Tool to execute bash commands in the agent's Docker container."""

    def __init__(self, docker_config: DockerRuntimeConfig, docker_runtime: DockerRuntime | None):
        """Initialize the tool.

        Args:
            docker_config: Docker runtime configuration
            docker_runtime: DockerRuntime instance (shared across tools)
        """
        super().__init__(
            name="executeBashCommand",
            description="在 Docker 容器中执行 bash 命令。用于运行 shell 命令、安装软件包、检查系统状态等。",
            parameters={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 bash 命令，支持管道、重定向及用分号分隔的多条命令。",
                    },
                    "background": {
                        "type": "boolean",
                        "description": "是否在后台执行。为 true 时立即返回 task_id，不阻塞 Agent 等待结果。之后可通过 checkBashTask 查询结果。",
                        "default": False,
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "最大执行时间（秒），默认 60 秒。",
                        "default": 60,
                        "minimum": 1,
                        "maximum": 300,
                    },
                },
                "required": ["command"],
            },
        )
        self._docker_config = docker_config
        self._docker_runtime = docker_runtime

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute a bash command.

        Args:
            command: The bash command to execute
            background: If true, run in background and return task_id immediately
            timeout: Maximum execution time in seconds

        Returns:
            ToolResult with stdout, stderr, and exit code
        """
        command: str = kwargs["command"]
        background: bool = kwargs.get("background", False)
        timeout: int = kwargs.get("timeout", 60)

        if self._docker_runtime is None:
            return ToolResult(
                success=False,
                data={},
                error="Docker runtime not initialized",
            )

        if background:
            return await self._execute_background(command, timeout)

        try:
            result = await self._docker_runtime.execute(command, timeout=timeout)

            return ToolResult(
                success=result.exit_code == 0,
                data={
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
                error=result.stderr if result.exit_code != 0 else "",
            )

        except Exception as e:
            return ToolResult(success=False, data={}, error=f"Failed to execute command: {str(e)}")

    async def _execute_background(self, command: str, timeout: int) -> ToolResult:
        """Execute a command in the background and return immediately."""
        if len(_background_tasks) >= _MAX_BACKGROUND_TASKS:
            return ToolResult(
                success=False,
                data={},
                error=f"Background task limit ({_MAX_BACKGROUND_TASKS}) reached.",
            )

        task_id = f"bg-{uuid.uuid4().hex[:12]}"

        docker_runtime = self._docker_runtime
        assert docker_runtime is not None

        async def _run() -> ExecutionResult:
            return await docker_runtime.execute(command, timeout=timeout)

        task = asyncio.create_task(_run())
        task.add_done_callback(lambda _: _background_tasks.pop(task_id, None))
        _background_tasks[task_id] = task

        return ToolResult(
            success=True,
            data={
                "task_id": task_id,
                "command": command[:200],
                "status": "running",
            },
            message=f"✅ 后台任务已启动 ({task_id})",
        )


class CheckBashTaskTool(Tool):
    """Tool to check the status of a background bash task."""

    def __init__(self) -> None:
        super().__init__(
            name="checkBashTask",
            description="检查后台 bash 任务的执行状态。通过 executeBashCommand 的 background=true 启动的任务，用此工具查询结果。",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "后台任务的 task_id",
                    },
                },
                "required": ["task_id"],
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Check the status of a background task.

        Args:
            task_id: Task ID returned by ExecuteBashTool

        Returns:
            ToolResult with task status and result if completed
        """
        task_id: str = kwargs["task_id"]

        task = _background_tasks.get(task_id)
        if task is None:
            return ToolResult(
                success=False,
                data={},
                error=f"Task '{task_id}' not found. It may have already completed.",
            )

        if not task.done():
            return ToolResult(
                success=True,
                data={
                    "task_id": task_id,
                    "status": "running",
                },
                message=f"⏳ 任务 {task_id} 正在执行中...",
            )

        try:
            result = task.result()
            return ToolResult(
                success=result.exit_code == 0,
                data={
                    "task_id": task_id,
                    "status": "completed",
                    "exit_code": result.exit_code,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
                error=result.stderr if result.exit_code != 0 else "",
                message=f"✅ 任务 {task_id} 已完成 (exit code: {result.exit_code})",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                data={
                    "task_id": task_id,
                    "status": "failed",
                },
                error=f"Task failed: {str(e)}",
            )
