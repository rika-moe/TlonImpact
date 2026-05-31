"""Write file tool — operates inside the sandbox container."""

from typing import TYPE_CHECKING

from tlon.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.base.config import DockerRuntimeConfig
    from tlon.sandbox.docker_runtime import DockerRuntime


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

    def __init__(self, docker_config: "DockerRuntimeConfig", docker_runtime: "DockerRuntime | None"):
        """Initialize the tool.

        Args:
            docker_config: Docker runtime configuration.
            docker_runtime: DockerRuntime instance (shared across tools)
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

    async def execute(self, file_path: str, content: str) -> ToolResult:
        """Write content to a file in the sandbox container.

        Args:
            file_path: Target file path (relative to container working dir).
            content: Full content to write.

        Returns:
            ToolResult with write status.
        """
        if self._docker_runtime is None:
            return ToolResult(
                success=False,
                data={},
                error="Docker runtime not initialized",
            )

        workdir = self._docker_config.working_dir
        container_path = f"{workdir}/{file_path}" if not file_path.startswith("/") else file_path

        # Ensure parent directories exist
        parent = "/".join(container_path.split("/")[:-1])
        if parent:
            await self._docker_runtime.execute(f"mkdir -p {parent}")

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
