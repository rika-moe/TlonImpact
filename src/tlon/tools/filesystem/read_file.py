"""Read file tool — operates inside the sandbox container."""

from typing import TYPE_CHECKING, Any

from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult
from tlon.tools.filesystem.path_guard import DEFAULT_ALLOWED_PATHS, resolve_container_path

logger = get_logger(__name__)

if TYPE_CHECKING:
    from tlon.base.config import DockerRuntimeConfig
    from tlon.sandbox.docker_runtime import DockerRuntime


class ReadFileTool(Tool):
    """Tool to read the contents of a file inside the agent's sandbox.

    All file operations are performed within the Docker container —
    the agent has no access to the host filesystem.

    Example:
        >>> tool = ReadFileTool(docker_config, agent_id)
        >>> result = await tool.execute(file_path="src/main.py", offset=1, limit=50)
    """

    _MAX_LIMIT = 500

    def __init__(
        self,
        docker_config: "DockerRuntimeConfig",
        docker_runtime: "DockerRuntime | None",
        allowed_paths: list[str] | None = None,
    ):
        """Initialize the tool.

        Args:
            docker_config: Docker runtime configuration.
            docker_runtime: DockerRuntime instance (shared across tools)
            allowed_paths: Whitelisted absolute container paths the tool may
                access. Defaults to the container working directory.
        """
        super().__init__(
            name="readFile",
            description=(
                "读取沙箱中的文件内容。用于查看代码、配置文件、日志等。"
                "支持指定起始行和读取行数，避免大文件导致上下文过长。"
                "自己的家目录以只读方式挂载在 /tlon-home"
                "（config/ 行为定义、memory/ 记忆、mail/ 邮箱等，可读不可写）。"
                "所有操作在隔离的 Docker 容器内执行。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "文件路径，相对于工作目录。例如：src/tlon/agents/agent.py",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（从1开始），默认从第1行开始读取",
                        "default": 1,
                        "minimum": 1,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多读取的行数，默认100，最大500",
                        "default": 100,
                        "minimum": 1,
                        "maximum": 500,
                    },
                },
                "required": ["file_path"],
            },
        )
        self._docker_config = docker_config
        self._docker_runtime = docker_runtime
        self._allowed_paths = allowed_paths or list(DEFAULT_ALLOWED_PATHS)

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Read a file from the sandbox container.

        Args:
            file_path: Path to the file (relative to container working dir).
            offset: Starting line number (1-based).
            limit: Maximum number of lines to read.

        Returns:
            ToolResult with file content and metadata.
        """
        file_path: str = kwargs["file_path"]
        offset: int = kwargs.get("offset", 1)
        limit: int = kwargs.get("limit", 100)

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

        try:
            raw = await self._docker_runtime.read_file(container_path)
        except FileNotFoundError:
            return ToolResult(
                success=False,
                data={},
                error=f"File not found: {file_path}",
            )
        except RuntimeError as e:
            logger.error("file_read_failed", file_path=file_path, error=str(e), exc_info=True)
            return ToolResult(
                success=False,
                data={},
                error=f"Failed to read file: {e}",
            )

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                success=False,
                data={},
                error=f"File is not UTF-8 text: {file_path}",
            )

        lines = text.splitlines()
        total_lines = len(lines)

        start = max(0, offset - 1)
        clamped_limit = min(limit, self._MAX_LIMIT)
        end = min(start + clamped_limit, total_lines)

        selected = lines[start:end]
        content = "\n".join(selected)

        if end < total_lines:
            content += f"\n\n... ({total_lines - end} more lines)"

        return ToolResult(
            success=True,
            data={
                "content": content,
                "file_path": file_path,
                "total_lines": total_lines,
                "read_lines": len(selected),
                "offset": start + 1,
            },
            message=f"📄 Read {len(selected)}/{total_lines} lines from {file_path}",
        )
