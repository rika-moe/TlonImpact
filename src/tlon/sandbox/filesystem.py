"""Member-bound sandbox file operations; no Tool or host filesystem access."""

from __future__ import annotations

import fnmatch
import re
import tarfile
import time
from typing import Any

from tlon.base.config import DockerRuntimeConfig
from tlon.base.logger import get_logger
from tlon.operations.core import CallContext
from tlon.operations.schema import OperationError, RejectedError, UnknownOutcomeError
from tlon.sandbox.archive import DirectoryArchive
from tlon.sandbox.device import ExecutionDevice
from tlon.sandbox.path_guard import resolve_container_path

logger = get_logger(__name__)


def _format_mtime(mtime: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M", time.gmtime(mtime))


def _under_dot_git(rel: str) -> bool:
    return ".git" in rel.split("/")


class MemberFilesystemService:
    """Host configuration binds device and path authority outside call arguments."""

    _MAX_LIMIT = 500
    _DEFAULT_MAX_ENTRIES = 200
    _MAX_ENTRIES = 1000
    _DEFAULT_MAX_RESULTS = 50
    _MAX_RESULTS = 200
    _MAX_LINE_CHARS = 200

    def __init__(
        self,
        config: DockerRuntimeConfig,
        device: ExecutionDevice | None,
        read_allowed_paths: list[str],
        write_allowed_paths: list[str],
    ) -> None:
        self._docker_config = config
        self._docker_runtime = device
        self._read_allowed_paths = list(read_allowed_paths)
        self._write_allowed_paths = list(write_allowed_paths)

    async def _path(self, path: str, *, write: bool = False) -> str:
        if self._docker_config.level == "L0":
            raise RejectedError("L0 members cannot access files", {})
        if self._docker_runtime is None:
            raise OperationError("Execution device not initialized")
        roots = self._write_allowed_paths if write else self._read_allowed_paths
        if write and (path == "/tlon-home" or path.startswith("/tlon-home/")):
            raise RejectedError("/tlon-home is read-only", {})
        try:
            resolved = resolve_container_path(path, self._docker_config.working_dir, roots)
            if write and (resolved == "/tlon-home" or resolved.startswith("/tlon-home/")):
                raise ValueError("/tlon-home is read-only")
            await self._docker_runtime.check_path(resolved, roots, write=write)
        except (ValueError, FileNotFoundError) as exc:
            raise RejectedError(str(exc), {}) from exc
        return resolved

    async def _write(self, path: str, content: bytes, context: CallContext) -> None:
        assert self._docker_runtime is not None
        try:
            accepted = await self._docker_runtime.write_file(path, content)
            if not accepted:
                raise RuntimeError("device did not confirm file write")
        except Exception as exc:
            raise UnknownOutcomeError(
                f"File write outcome unknown: {exc}",
                {"outcome": "unknown", "call_id": context.call_id, "retry_safe": False},
            ) from exc

    @staticmethod
    def _archive(tar_bytes: bytes, path: str) -> DirectoryArchive:
        try:
            return DirectoryArchive(tar_bytes, path)
        except (tarfile.TarError, ValueError, EOFError) as exc:
            raise OperationError(f"Invalid sandbox archive: {exc}") from exc

    async def read(self, arguments: dict[str, Any], context: CallContext) -> dict[str, Any]:
        file_path: str = arguments["file_path"]
        offset: int = arguments.get("offset", 1)
        limit: int = arguments.get("limit", 100)
        if self._docker_runtime is None:
            raise OperationError("Docker runtime not initialized")
        container_path = await self._path(file_path)
        try:
            raw = await self._docker_runtime.read_file(container_path)
        except FileNotFoundError:
            raise RejectedError(f"File not found: {file_path}", {}) from None
        except RuntimeError as e:
            logger.error("file_read_failed", file_path=file_path, error=str(e), exc_info=True)
            raise OperationError(f"Failed to read file: {e}") from e
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise RejectedError(f"File is not UTF-8 text: {file_path}", {}) from None
        lines = text.splitlines()
        total_lines = len(lines)
        start = max(0, offset - 1)
        clamped_limit = min(limit, self._MAX_LIMIT)
        end = min(start + clamped_limit, total_lines)
        selected = lines[start:end]
        content = "\n".join(selected)
        if end < total_lines:
            content += f"\n\n... ({total_lines - end} more lines)"
        return {
            "content": content,
            "file_path": file_path,
            "total_lines": total_lines,
            "read_lines": len(selected),
            "offset": start + 1,
            "message": f"📄 Read {len(selected)}/{total_lines} lines from {file_path}",
        }

    async def write(self, arguments: dict[str, Any], context: CallContext) -> dict[str, Any]:
        file_path: str = arguments["file_path"]
        content: str = arguments["content"]
        if self._docker_runtime is None:
            raise OperationError("Docker runtime not initialized")
        container_path = await self._path(file_path, write=True)
        try:
            existed = await self._docker_runtime.file_exists(container_path)
        except RuntimeError as exc:
            raise OperationError(str(exc)) from exc
        await self._write(container_path, content.encode("utf-8"), context)
        bytes_written = len(content.encode("utf-8"))
        return {
            "file_path": file_path,
            "bytes_written": bytes_written,
            "existed_before": existed,
            "message": f"{('📝 Overwritten' if existed else '✅ Created')} {file_path} ({bytes_written} bytes)",
        }

    async def edit(self, arguments: dict[str, Any], context: CallContext) -> dict[str, Any]:
        file_path: str = arguments["file_path"]
        old_string: str = arguments["old_string"]
        new_string: str = arguments["new_string"]
        if self._docker_runtime is None:
            raise OperationError("Docker runtime not initialized")
        container_path = await self._path(file_path, write=True)
        try:
            raw = await self._docker_runtime.read_file(container_path)
        except FileNotFoundError:
            raise RejectedError(f"File not found: {file_path}", {}) from None
        except RuntimeError as e:
            raise OperationError(f"Failed to read file: {e}") from e
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise RejectedError(f"File is not UTF-8 text: {file_path}", {}) from None
        occurrences = content.count(old_string)
        if occurrences == 0:
            raise RejectedError(
                f"old_string not found in {file_path}. The text you provided does not appear in the file.",
                {},
            )
        if occurrences > 1:
            raise RejectedError(
                f"old_string is ambiguous ({occurrences} matches in {file_path}). Please provide a more specific string with more context.",
                {},
            )
        new_content = content.replace(old_string, new_string, 1)
        await self._write(container_path, new_content.encode("utf-8"), context)
        return {
            "file_path": file_path,
            "replaced": True,
            "old_length": len(old_string),
            "new_length": len(new_string),
            "message": f"✏️ Edited {file_path} ({len(old_string)} → {len(new_string)} chars)",
        }

    async def list_directory(
        self, arguments: dict[str, Any], context: CallContext
    ) -> dict[str, Any]:
        path: str = arguments.get("path", ".")
        max_entries: int = arguments.get("max_entries", self._DEFAULT_MAX_ENTRIES)
        if self._docker_runtime is None:
            raise OperationError("Docker runtime not initialized")
        container_path = await self._path(path)
        limit = min(max(1, max_entries), self._MAX_ENTRIES)
        try:
            tar_bytes = await self._docker_runtime.fetch_archive(container_path)
        except FileNotFoundError:
            raise RejectedError(f"Directory not found: {path}", {}) from None
        except RuntimeError as e:
            logger.error("dir_list_failed", path=path, error=str(e))
            raise OperationError(f"Failed to list directory: {e}") from e
        archive = self._archive(tar_bytes, container_path)
        if not archive.root_is_dir:
            raise RejectedError(f"Not a directory: {path}", {})
        children = sorted(
            [e for e in archive.entries if "/" not in e.rel], key=lambda e: (not e.is_dir, e.name)
        )
        truncated = len(children) > limit
        entries = [
            {
                "name": e.name,
                "type": e.type_name,
                "size": e.size,
                "modified": _format_mtime(e.mtime),
            }
            for e in children[:limit]
        ]
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"]))
        data: dict[str, Any] = {
            "path": path,
            "entries": entries,
            "count": len(entries),
            "truncated": truncated,
        }
        if truncated:
            data["hint"] = f"目录条目超过 {limit}，已截断。可缩小到子目录再列。"
        return {
            **data,
            "message": f"📂 {path}: {len(entries)} entries"
            + (f" (truncated at {limit})" if truncated else ""),
        }

    async def search(self, arguments: dict[str, Any], context: CallContext) -> dict[str, Any]:
        pattern: str = arguments["pattern"]
        path: str = arguments.get("path", ".")
        mode: str = arguments.get("mode", "content")
        max_results: int = arguments.get("max_results", self._DEFAULT_MAX_RESULTS)
        if mode not in ("content", "filename"):
            raise RejectedError(f"Invalid mode: {mode!r} (expected 'content' or 'filename')", {})
        if self._docker_runtime is None:
            raise OperationError("Docker runtime not initialized")
        container_path = await self._path(path)
        regex: re.Pattern[str] | None = None
        if mode == "content":
            try:
                regex = re.compile(pattern)
            except re.error as e:
                raise RejectedError(f"Search failed: {e}", {}) from e
        limit = min(max(1, max_results), self._MAX_RESULTS)
        try:
            tar_bytes = await self._docker_runtime.fetch_archive(container_path)
        except FileNotFoundError:
            raise RejectedError(f"Path not found: {path}", {}) from None
        except RuntimeError as e:
            logger.error("search_failed", pattern=pattern, path=path, error=str(e))
            raise OperationError(f"Search failed: {e}") from e
        archive = self._archive(tar_bytes, container_path)
        if mode == "filename" and (not archive.root_is_dir):
            raise RejectedError(f"Path not found: {path}", {})
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
        return {
            **data,
            "message": f"🔍 {len(matches)} matches"
            + (f" (truncated at {limit})" if truncated else ""),
        }

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
                    return (matches[:limit], True)
        return (matches, False)

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
                    return (matches[:limit], True)
        return (matches, False)
