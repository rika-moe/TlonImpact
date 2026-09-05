"""List directory tool — operates inside the sandbox container."""

import shlex
from typing import TYPE_CHECKING, Any

from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult
from tlon.tools.filesystem.path_guard import DEFAULT_ALLOWED_PATHS, resolve_container_path

logger = get_logger(__name__)

if TYPE_CHECKING:
    from tlon.base.config import DockerRuntimeConfig
    from tlon.sandbox.docker_runtime import DockerRuntime

# find -printf %y single-letter types → display names
_TYPE_NAMES = {"d": "dir", "f": "file", "l": "symlink"}


class ListDirectoryTool(Tool):
    """Tool to list a directory's contents inside the agent's sandbox.

    Runs ``find -maxdepth 1`` in the Docker container and returns a
    structured, bounded entry list (name / type / size / mtime).

    Example:
        >>> tool = ListDirectoryTool(docker_config, docker_runtime)
        >>> result = await tool.execute(path="src/tlon")
    """

    _DEFAULT_MAX_ENTRIES = 200
    _MAX_ENTRIES = 1000

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
            name="listDirectory",
            description=(
                "列出沙箱中目录的内容（名称/类型/大小/修改时间，目录在前按名排序）。"
                "查看目录结构时优先用此工具，不要用 executeBashCommand 跑 ls。"
                "条目数有上限，超出会截断并注明。所有操作在隔离的 Docker 容器内执行。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径，相对于工作目录，默认当前工作目录。例如：src/tlon",
                        "default": ".",
                    },
                    "max_entries": {
                        "type": "integer",
                        "description": "最多返回的条目数，默认200，最大1000",
                        "default": 200,
                        "minimum": 1,
                        "maximum": 1000,
                    },
                },
                "required": [],
            },
        )
        self._docker_config = docker_config
        self._docker_runtime = docker_runtime
        self._allowed_paths = allowed_paths or list(DEFAULT_ALLOWED_PATHS)

    async def execute(self, **kwargs: Any) -> ToolResult:
        """List one directory level in the sandbox container.

        Args:
            path: Directory path (relative to container working dir).
            max_entries: Maximum number of entries to return.

        Returns:
            ToolResult with the bounded entry list.
        """
        path: str = kwargs.get("path", ".")
        max_entries: int = kwargs.get("max_entries", self._DEFAULT_MAX_ENTRIES)

        if self._docker_runtime is None:
            return ToolResult(
                success=False,
                data={},
                error="Docker runtime not initialized",
            )

        # Resolve and sandbox-check the path (POSIX container semantics)
        try:
            container_path = resolve_container_path(
                path, self._docker_config.working_dir, self._allowed_paths
            )
        except ValueError as e:
            return ToolResult(
                success=False,
                data={},
                error=str(e),
            )

        limit = min(max(1, max_entries), self._MAX_ENTRIES)
        quoted = shlex.quote(container_path)
        # %y=type letter, %s=size, %T*=mtime, %f=basename; tab-separated for
        # robust parsing. head bounds the listing at the shell level.
        command = (
            f"test -e {quoted} || {{ echo '__TLON_NOT_FOUND__' >&2; exit 1; }}; "
            f"test -d {quoted} || {{ echo '__TLON_NOT_A_DIRECTORY__' >&2; exit 1; }}; "
            f"find {quoted} -mindepth 1 -maxdepth 1 "
            "-printf '%y\\t%s\\t%TY-%Tm-%TdT%TH:%TM\\t%f\\n' "
            f"| head -n {limit + 1}"
        )
        result = await self._docker_runtime.execute(command)
        if result.exit_code != 0:
            stderr = result.stderr.strip()
            if "__TLON_NOT_FOUND__" in stderr:
                error = f"Directory not found: {path}"
            elif "__TLON_NOT_A_DIRECTORY__" in stderr:
                error = f"Not a directory: {path}"
            else:
                logger.error("dir_list_failed", path=path, stderr=stderr)
                error = f"Failed to list directory: {stderr}"
            return ToolResult(success=False, data={}, error=error)

        lines = [line for line in result.stdout.splitlines() if line]
        truncated = len(lines) > limit
        entries = []
        for line in lines[:limit]:
            parts = line.split("\t", 3)
            if len(parts) != 4:
                continue
            type_char, size, modified, name = parts
            entries.append(
                {
                    "name": name,
                    "type": _TYPE_NAMES.get(type_char, type_char),
                    "size": int(size),
                    "modified": modified,
                }
            )
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"]))

        data: dict[str, Any] = {
            "path": path,
            "entries": entries,
            "count": len(entries),
            "truncated": truncated,
        }
        if truncated:
            data["hint"] = f"目录条目超过 {limit}，已截断。可缩小到子目录再列。"

        return ToolResult(
            success=True,
            data=data,
            message=(
                f"📂 {path}: {len(entries)} entries"
                + (f" (truncated at {limit})" if truncated else "")
            ),
        )
