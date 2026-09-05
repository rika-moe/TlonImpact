"""Search files tool — filename glob and content grep inside the sandbox."""

import shlex
from typing import TYPE_CHECKING, Any

from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult
from tlon.tools.filesystem.path_guard import DEFAULT_ALLOWED_PATHS, resolve_container_path

logger = get_logger(__name__)

if TYPE_CHECKING:
    from tlon.base.config import DockerRuntimeConfig
    from tlon.sandbox.docker_runtime import DockerRuntime


class SearchFilesTool(Tool):
    """Tool to search files inside the agent's sandbox.

    Two modes: ``filename`` (find -iname glob) and ``content`` (recursive
    extended-regex grep with line numbers). Both run in the Docker container
    and return bounded results: a match cap plus per-line truncation.

    Example:
        >>> tool = SearchFilesTool(docker_config, docker_runtime)
        >>> result = await tool.execute(pattern="def main", path="src")
    """

    _DEFAULT_MAX_RESULTS = 50
    _MAX_RESULTS = 200
    # Per-line display cap; the shell additionally hard-caps lines at
    # _SHELL_LINE_CAP chars so minified/binary-ish lines cannot blow up
    # the exec result before Python sees it.
    _MAX_LINE_CHARS = 200
    _SHELL_LINE_CAP = 1000

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
            name="searchFiles",
            description=(
                "在沙箱中搜索文件。mode=content（默认）按内容做正则检索（grep -E 语法），"
                "返回匹配的文件、行号和行内容；mode=filename 按文件名 glob 查找（如 *.py，不区分大小写）。"
                "搜索文件优先用此工具，不要用 executeBashCommand 跑 grep/find。"
                "结果有界：匹配数上限 + 每行长度截断，超出会注明。"
                "自己的家目录以只读方式挂载在 /tlon-home，可作为搜索起点。"
                "所有操作在隔离的 Docker 容器内执行。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "搜索模式：content 模式是扩展正则（grep -E），filename 模式是 glob（如 *.log）",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索起点目录，相对于工作目录，默认当前工作目录",
                        "default": ".",
                    },
                    "mode": {
                        "type": "string",
                        "description": "content=按内容检索（默认）；filename=按文件名查找",
                        "enum": ["content", "filename"],
                        "default": "content",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最多返回的匹配数，默认50，最大200",
                        "default": 50,
                        "minimum": 1,
                        "maximum": 200,
                    },
                },
                "required": ["pattern"],
            },
        )
        self._docker_config = docker_config
        self._docker_runtime = docker_runtime
        self._allowed_paths = allowed_paths or list(DEFAULT_ALLOWED_PATHS)

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Search files in the sandbox container.

        Args:
            pattern: Extended regex (content mode) or glob (filename mode).
            path: Search root (relative to container working dir).
            mode: "content" or "filename".
            max_results: Maximum number of matches to return.

        Returns:
            ToolResult with the bounded match list.
        """
        pattern: str = kwargs["pattern"]
        path: str = kwargs.get("path", ".")
        mode: str = kwargs.get("mode", "content")
        max_results: int = kwargs.get("max_results", self._DEFAULT_MAX_RESULTS)

        if mode not in ("content", "filename"):
            return ToolResult(
                success=False,
                data={},
                error=f"Invalid mode: {mode!r} (expected 'content' or 'filename')",
            )

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

        limit = min(max(1, max_results), self._MAX_RESULTS)
        quoted_path = shlex.quote(container_path)
        quoted_pattern = shlex.quote(pattern)
        # -s silences per-file noise (unreadable files), so anything left on
        # stderr is a real error (e.g. invalid regex). grep --null separates
        # filename from "lineno:text" with NUL, safe against colons in names.
        if mode == "content":
            command = (
                f"test -e {quoted_path} || {{ echo '__TLON_NOT_FOUND__' >&2; exit 1; }}; "
                f"grep -rnIsE --null --exclude-dir=.git -- {quoted_pattern} {quoted_path} "
                f"| head -n {limit + 1} | cut -c 1-{self._SHELL_LINE_CAP}"
            )
        else:
            command = (
                f"test -d {quoted_path} || {{ echo '__TLON_NOT_FOUND__' >&2; exit 1; }}; "
                f"find {quoted_path} -iname {quoted_pattern} -not -path '*/.git/*' "
                f"| head -n {limit + 1}"
            )

        result = await self._docker_runtime.execute(command)
        stderr = result.stderr.strip()
        if result.exit_code != 0 or stderr:
            if "__TLON_NOT_FOUND__" in stderr:
                error = f"Path not found: {path}"
            else:
                logger.error("search_failed", pattern=pattern, path=path, stderr=stderr)
                error = f"Search failed: {stderr}"
            return ToolResult(success=False, data={}, error=error)

        lines = [line for line in result.stdout.splitlines() if line]
        truncated = len(lines) > limit
        lines = lines[:limit]

        matches: list[Any]
        if mode == "content":
            matches = []
            for raw in lines:
                if "\0" not in raw:
                    continue
                file, _, rest = raw.partition("\0")
                line_no, _, text = rest.partition(":")
                if len(text) > self._MAX_LINE_CHARS:
                    text = text[: self._MAX_LINE_CHARS] + "…"
                matches.append(
                    {
                        "file": file,
                        "line": int(line_no) if line_no.isdigit() else 0,
                        "text": text,
                    }
                )
        else:
            matches = lines

        data: dict[str, Any] = {
            "mode": mode,
            "pattern": pattern,
            "path": path,
            "matches": matches,
            "count": len(matches),
            "truncated": truncated,
        }
        if truncated:
            data["hint"] = f"匹配超过 {limit} 条，已截断。可缩小 path 或改精确 pattern。"

        return ToolResult(
            success=True,
            data=data,
            message=(
                f"🔍 {len(matches)} matches" + (f" (truncated at {limit})" if truncated else "")
            ),
        )
