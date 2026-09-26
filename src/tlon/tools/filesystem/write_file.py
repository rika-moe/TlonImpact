"""Write file tool — operates inside the sandbox container."""

from typing import TYPE_CHECKING, Any

from tlon.tools.base import Tool, ToolResult
from tlon.tools.filesystem.path_guard import DEFAULT_ALLOWED_PATHS, resolve_container_path

if TYPE_CHECKING:
    from tlon.base.config import DockerRuntimeConfig
    from tlon.sandbox.device import ExecutionDevice


class WriteFileTool(Tool):
    """Tool to write content to a file inside the agent's sandbox.

    All file operations are performed within the Docker container —
    the agent has no access to the host filesystem.

    Example:
        >>> tool = WriteFileTool(docker_config, agent_id)
        >>> result = await tool.execute(
        ...     file_path="src/new_module.py",
        ...     content="def hello():\\n    return 'world'\\n"
        ... )
    """

    def __init__(
        self,
        docker_config: "DockerRuntimeConfig",
        docker_runtime: "ExecutionDevice | None",
        allowed_paths: list[str] | None = None,
    ):
        """Initialize the tool.

        Args:
            docker_config: Docker runtime configuration.
            docker_runtime: ExecutionDevice instance (shared across tools)
            allowed_paths: Whitelisted absolute container paths the tool may
                access. Defaults to the container working directory.
        """
        super().__init__(
            name="writeFile",
            description=(
                "将内容写入沙箱中的文件。如果文件不存在则创建，"
                "自动创建父目录。用于创建新文件或完全重写已有文件。"
                "所有操作在隔离的 Docker 容器内执行。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "目标文件路径，相对于工作目录。例如：src/utils/helper.py",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入文件的完整内容",
                    },
                },
                "required": ["file_path", "content"],
            },
        )
        self._docker_config = docker_config
        self._docker_runtime = docker_runtime
        self._allowed_paths = allowed_paths or list(DEFAULT_ALLOWED_PATHS)

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Write content to a file in the sandbox container.

        Args:
            file_path: Target file path (relative to container working dir).
            content: Full content to write.

        Returns:
            ToolResult with write status.
        """
        file_path: str = kwargs["file_path"]
        content: str = kwargs["content"]

        if self._docker_runtime is None:
            return ToolResult(
                success=False,
                data={},
                error="Docker runtime not initialized",
            )

        # Resolve and sandbox-check the path (POSIX container semantics)
        try:
            container_path = resolve_container_path(
                file_path, self._docker_config.working_dir, self._allowed_paths
            )
        except ValueError as e:
            return ToolResult(
                success=False,
                data={},
                error=str(e),
            )

        # Ensure parent directories exist
        parent = "/".join(container_path.split("/")[:-1])
        if parent:
            await self._docker_runtime.run(f"mkdir -p {parent}")

        try:
            existed = await self._docker_runtime.file_exists(container_path)
        except RuntimeError:
            existed = False

        try:
            await self._docker_runtime.write_file(
                container_path,
                content.encode("utf-8"),
            )
        except RuntimeError as e:
            return ToolResult(
                success=False,
                data={},
                error=f"Failed to write file: {e}",
            )

        bytes_written = len(content.encode("utf-8"))

        return ToolResult(
            success=True,
            data={
                "file_path": file_path,
                "bytes_written": bytes_written,
                "existed_before": existed,
            },
            message=(
                f"{'📝 Overwritten' if existed else '✅ Created'} "
                f"{file_path} ({bytes_written} bytes)"
            ),
        )
