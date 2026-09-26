"""Search files tool — filename glob and content grep inside the sandbox."""

import fnmatch
import re
from typing import TYPE_CHECKING, Any

from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult
from tlon.tools.filesystem.archive import DirectoryArchive
from tlon.tools.filesystem.path_guard import DEFAULT_ALLOWED_PATHS, resolve_container_path

logger = get_logger(__name__)

if TYPE_CHECKING:
    from tlon.base.config import DockerRuntimeConfig
    from tlon.sandbox.device import ExecutionDevice


def _under_dot_git(rel: str) -> bool:
    """True if a root-relative path lives under a ``.git`` directory
    (the old ``-not -path '*/.git/*'`` / ``--exclude-dir=.git`` filter)."""
    return ".git" in rel.split("/")


class SearchFilesTool(Tool):
    """Tool to search files inside the agent's sandbox.

    Two modes: ``filename`` (case-insensitive glob on basenames) and
    ``content`` (regex with line numbers). Both fetch the search root as
    a tar archive (get_archive) and match host-side (issue #40) — no
    find/grep runs in the container, so the tool works at L1 where the
    file cabinet never starts. Results stay bounded: a match cap plus
    per-line truncation.

    Example:
        >>> tool = SearchFilesTool(docker_config, docker_runtime)
        >>> result = await tool.execute(pattern="def main", path="src")
    """

    _DEFAULT_MAX_RESULTS = 50
    _MAX_RESULTS = 200
    # Per-line display cap.
    _MAX_LINE_CHARS = 200

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
            name="searchFiles",
            description=(
                "在沙箱中搜索文件。mode=content（默认）按内容做正则检索，"
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
                        "description": "搜索模式：content 模式是正则，filename 模式是 glob（如 *.log）",
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
            pattern: Regex (content mode) or glob (filename mode).
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

        regex: re.Pattern[str] | None = None
        if mode == "content":
            try:
                regex = re.compile(pattern)
            except re.error as e:
                return ToolResult(success=False, data={}, error=f"Search failed: {e}")

        limit = min(max(1, max_results), self._MAX_RESULTS)
        try:
            tar_bytes = await self._docker_runtime.fetch_archive(container_path)
        except FileNotFoundError:
            return ToolResult(success=False, data={}, error=f"Path not found: {path}")
        except RuntimeError as e:
            logger.error("search_failed", pattern=pattern, path=path, error=str(e))
            return ToolResult(success=False, data={}, error=f"Search failed: {e}")

        archive = DirectoryArchive(tar_bytes, container_path)
        if mode == "filename" and not archive.root_is_dir:
            # find required a directory root (test -d) — keep the contract
            return ToolResult(success=False, data={}, error=f"Path not found: {path}")

        matches: list[Any]
        truncated: bool
        if mode == "filename":
            matches, truncated = self._match_filenames(archive, pattern, limit)
        else:
            assert regex is not None
            matches, truncated = self._match_contents(archive, regex, limit)

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

    def _match_filenames(
        self, archive: DirectoryArchive, pattern: str, limit: int
    ) -> tuple[list[str], bool]:
        """Case-insensitive glob over basenames (the old find -iname),
        .git subtrees excluded."""
        glob = pattern.lower()
        matches: list[str] = []
        for entry in archive.entries:
            if _under_dot_git(entry.rel):
                continue
            if fnmatch.fnmatch(entry.name.lower(), glob):
                matches.append(entry.path)
                if len(matches) > limit:
                    return matches[:limit], True
        return matches, False

    def _match_contents(
        self, archive: DirectoryArchive, regex: re.Pattern[str], limit: int
    ) -> tuple[list[dict[str, Any]], bool]:
        """Regex search with line numbers (the old grep -rnIsE):
        .git subtrees and binary (NUL-containing) files excluded."""
        matches: list[dict[str, Any]] = []
        for entry in archive.entries:
            if entry.is_dir or entry.is_symlink or _under_dot_git(entry.rel):
                continue
            data = archive.read(entry)
            if b"\x00" in data:
                continue
            for lineno, line in enumerate(
                data.decode("utf-8", errors="replace").splitlines(), start=1
            ):
                if not regex.search(line):
                    continue
                if len(line) > self._MAX_LINE_CHARS:
                    line = line[: self._MAX_LINE_CHARS] + "…"
                matches.append({"file": entry.path, "line": lineno, "text": line})
                if len(matches) > limit:
                    return matches[:limit], True
        return matches, False
