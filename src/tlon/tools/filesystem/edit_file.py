"""Edit file tool — operates inside the sandbox container."""

from typing import TYPE_CHECKING, Any

from tlon.tools.base import Tool, ToolResult
from tlon.tools.filesystem.path_guard import DEFAULT_ALLOWED_PATHS, resolve_container_path

if TYPE_CHECKING:
    from tlon.base.config import DockerRuntimeConfig
    from tlon.sandbox.device import ExecutionDevice


class EditFileTool(Tool):
    """Tool to edit a file by replacing a specific text segment inside the sandbox.

    Uses the old_string/new_string model (inspired by Anthropic/Cursor):
    - old_string must appear exactly once in the file
    - If old_string appears 0 times → error
    - If old_string appears 2+ times → error (ambiguous)
    - If unique match found → replaced with new_string

    All file operations are performed within the Docker container —
    the agent has no access to the host filesystem.

    Example:
        >>> tool = EditFileTool(docker_config, agent_id)
        >>> result = await tool.execute(
        ...     file_path="src/main.py",
        ...     old_string="def old_func():\\n    return 1",
        ...     new_string="def new_func():\\n    return 42",
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
            name="editFile",
            description=(
                "精准编辑沙箱中的文件内容。通过指定旧文本和新文本来替换文件中的特定片段。"
                "old_string 必须在文件中唯一匹配（出现且仅出现一次），否则编辑失败。"
                "所有操作在隔离的 Docker 容器内执行。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "目标文件路径，相对于工作目录",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "要被替换的精确文本片段。必须包含足够的上下文以确保唯一匹配。",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "替换后的新文本",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        )
        self._docker_config = docker_config
        self._docker_runtime = docker_runtime
        self._allowed_paths = allowed_paths or list(DEFAULT_ALLOWED_PATHS)

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Edit a file by replacing old_string with new_string in the sandbox.

        Args:
            file_path: Target file path (relative to container working dir).
            old_string: Exact text to replace (must be unique in file).
            new_string: Replacement text.

        Returns:
            ToolResult with edit status.
        """
        file_path: str = kwargs["file_path"]
        old_string: str = kwargs["old_string"]
        new_string: str = kwargs["new_string"]

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

        # Read file from container
        try:
            raw = await self._docker_runtime.read_file(container_path)
        except FileNotFoundError:
            return ToolResult(
                success=False,
                data={},
                error=f"File not found: {file_path}",
            )
        except RuntimeError as e:
            return ToolResult(
                success=False,
                data={},
                error=f"Failed to read file: {e}",
            )

        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                success=False,
                data={},
                error=f"File is not UTF-8 text: {file_path}",
            )

        # Validate old_string uniqueness
        occurrences = content.count(old_string)
        if occurrences == 0:
            return ToolResult(
                success=False,
                data={},
                error=(
                    f"old_string not found in {file_path}. "
                    f"The text you provided does not appear in the file."
                ),
            )
        if occurrences > 1:
            return ToolResult(
                success=False,
                data={},
                error=(
                    f"old_string is ambiguous ({occurrences} matches in {file_path}). "
                    f"Please provide a more specific string with more context."
                ),
            )

        # Perform replacement
        new_content = content.replace(old_string, new_string, 1)

        # Write back to container
        try:
            await self._docker_runtime.write_file(
                container_path,
                new_content.encode("utf-8"),
            )
        except RuntimeError as e:
            return ToolResult(
                success=False,
                data={},
                error=f"Failed to write file: {e}",
            )

        return ToolResult(
            success=True,
            data={
                "file_path": file_path,
                "replaced": True,
                "old_length": len(old_string),
                "new_length": len(new_string),
            },
            message=f"✏️ Edited {file_path} ({len(old_string)} → {len(new_string)} chars)",
        )
