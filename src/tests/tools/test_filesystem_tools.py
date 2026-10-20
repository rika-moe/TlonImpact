"""Tests for filesystem tools (ReadFile, WriteFile, EditFile, ListDirectory,
SearchFiles) via the ExecutionDevice interface."""

import io
import tarfile
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tlon.base.config import DockerRuntimeConfig, ToolConfig
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.filesystem import make_filesystem_operations
from tlon.sandbox.filesystem import MemberFilesystemService
from tlon.sandbox.path_guard import normalize_container_path, resolve_container_path


class _Projection:
    """Tests inspect the device boundary while calls use the real contract."""

    def __init__(self, name, config=None, device=None):
        self.name = name
        self._docker_config = config or DockerRuntimeConfig()
        self._docker_runtime = device
        self._allowed_paths = ["/workspace"]

    async def execute_context(self, arguments):
        service = MemberFilesystemService(
            self._docker_config, self._docker_runtime, self._allowed_paths, self._allowed_paths
        )
        operation = next(op for op in make_filesystem_operations(service) if op.name == self.name)
        executor = OperationExecutor(
            OperationRegistry([operation]), CallContext("alice", "run", "call")
        )
        return await OperationTool(executor, operation.name).execute_context({**arguments})


def _projection(name, config=None, device=None):
    return _Projection(name, config, device)


def _make_mock_runtime():
    """Create a mock ExecutionDevice with async file I/O methods."""
    runtime = MagicMock()
    runtime.check_path = AsyncMock()
    runtime.read_file = AsyncMock()
    runtime.write_file = AsyncMock(return_value=True)
    runtime.file_exists = AsyncMock(return_value=False)
    runtime.fetch_archive = AsyncMock()
    runtime.run = AsyncMock(return_value=MagicMock(exit_code=0, stdout="", stderr=""))
    return runtime


def _epoch(iso: str) -> float:
    """'2026-09-04T09:10' (UTC, container clock) → epoch seconds."""
    return datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp()


def _make_dir_tar(members: list[tuple[str, bytes | str | None, float]]) -> bytes:
    """Build a get_archive-style directory tar in memory.

    Each member is (name, payload, mtime): payload None = directory,
    bytes = file, str = symlink target.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, payload, mtime in members:
            info = tarfile.TarInfo(name=name)
            info.mtime = mtime
            if payload is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif isinstance(payload, str):
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                tar.addfile(info)
            else:
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


class TestReadFileTool:
    @pytest.fixture
    def read_tool(self):
        tool = _projection("file-read")
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()
        return tool

    async def test_read_full_file(self, read_tool):
        content = "Line 1\nLine 2\nLine 3\n"
        read_tool._docker_runtime.read_file.return_value = content.encode("utf-8")

        result = await read_tool.execute_context({"file_path": "test.txt"})

        assert result.success is True
        assert result.data["file_path"] == "test.txt"
        assert result.data["total_lines"] == 3
        assert result.data["read_lines"] == 3
        assert "Line 1" in result.data["content"]

        read_tool._docker_runtime.read_file.assert_awaited_once_with("/workspace/test.txt")

    async def test_read_with_offset_and_limit(self, read_tool):
        content = "\n".join([f"Line {i}" for i in range(1, 21)]) + "\n"
        read_tool._docker_runtime.read_file.return_value = content.encode("utf-8")

        result = await read_tool.execute_context({"file_path": "test.txt", "offset": 5, "limit": 3})

        assert result.success is True
        assert result.data["read_lines"] == 3
        assert result.data["offset"] == 5
        assert "Line 5" in result.data["content"]
        assert "Line 7" in result.data["content"]
        assert "Line 4" not in result.data["content"]

    async def test_read_with_limit_truncation(self, read_tool):
        lines = [f"Line {i}" for i in range(1, 201)]
        read_tool._docker_runtime.read_file.return_value = ("\n".join(lines)).encode("utf-8")

        result = await read_tool.execute_context({"file_path": "test.txt", "limit": 50})

        assert result.success is True
        assert result.data["read_lines"] == 50
        assert "... (150 more lines)" in result.data["content"]

    async def test_read_max_limit_enforced(self, read_tool):
        lines = [f"Line {i}" for i in range(1, 600)]
        read_tool._docker_runtime.read_file.return_value = ("\n".join(lines)).encode("utf-8")

        result = await read_tool.execute_context({"file_path": "test.txt", "limit": 1000})

        assert result.success is False
        assert "500" in result.error

    async def test_read_file_not_found(self, read_tool):
        read_tool._docker_runtime.read_file.side_effect = FileNotFoundError("not found")

        result = await read_tool.execute_context({"file_path": "missing.txt"})

        assert result.success is False
        assert "not found" in result.error.lower()

    async def test_read_runtime_error(self, read_tool):
        read_tool._docker_runtime.read_file.side_effect = RuntimeError("container crashed")

        result = await read_tool.execute_context({"file_path": "test.txt"})

        assert result.success is False
        assert "container crashed" in result.error.lower()

    async def test_read_runtime_not_initialized(self, read_tool):
        read_tool._docker_runtime = None

        result = await read_tool.execute_context({"file_path": "test.txt"})

        assert result.success is False
        assert "not initialized" in result.error.lower()


class TestWriteFileTool:
    @pytest.fixture
    def write_tool(self):
        tool = _projection("file-write")
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()
        return tool

    async def test_write_new_file(self, write_tool):
        write_tool._docker_runtime.file_exists.return_value = False

        result = await write_tool.execute_context(
            {"file_path": "new_file.txt", "content": "Hello, world!"}
        )

        assert result.success is True
        assert result.data["file_path"] == "new_file.txt"
        assert result.data["bytes_written"] == 13
        assert result.data["existed_before"] is False

        write_tool._docker_runtime.write_file.assert_awaited_once_with(
            "/workspace/new_file.txt",
            b"Hello, world!",
        )

    async def test_write_creates_parent_dirs(self, write_tool):
        """父目录创建由装置层 write_file 负责（tar 里带目录条目，issue
        #40）——工具自身不再 shell out mkdir。"""
        write_tool._docker_runtime.file_exists.return_value = False

        result = await write_tool.execute_context(
            {"file_path": "deep/nested/file.txt", "content": "nested"}
        )

        assert result.success is True
        write_tool._docker_runtime.run.assert_not_awaited()
        write_tool._docker_runtime.write_file.assert_awaited_once_with(
            "/workspace/deep/nested/file.txt",
            b"nested",
        )

    async def test_overwrite_existing_file(self, write_tool):
        write_tool._docker_runtime.file_exists.return_value = True

        result = await write_tool.execute_context(
            {"file_path": "existing.txt", "content": "new content"}
        )

        assert result.success is True
        assert result.data["existed_before"] is True

    async def test_write_runtime_not_initialized(self, write_tool):
        write_tool._docker_runtime = None

        result = await write_tool.execute_context({"file_path": "test.txt", "content": "x"})

        assert result.success is False
        assert "not initialized" in result.error.lower()


class TestEditFileTool:
    @pytest.fixture
    def edit_tool(self):
        tool = _projection("file-edit")
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()
        return tool

    async def test_edit_success(self, edit_tool):
        original = "def old_func():\n    return 1\n\ndef another():\n    pass\n"
        edit_tool._docker_runtime.read_file.return_value = original.encode("utf-8")

        result = await edit_tool.execute_context(
            {
                "file_path": "editable.py",
                "old_string": "def old_func():\n    return 1",
                "new_string": "def new_func():\n    return 42",
            }
        )

        assert result.success is True
        assert result.data["replaced"] is True

        call_args = edit_tool._docker_runtime.write_file.await_args
        written_bytes = call_args[0][1]
        written_text = written_bytes.decode("utf-8")
        assert "def new_func():" in written_text
        assert "return 42" in written_text
        assert "def old_func():" not in written_text

    async def test_edit_old_string_not_found(self, edit_tool):
        edit_tool._docker_runtime.read_file.return_value = b"some content"

        result = await edit_tool.execute_context(
            {
                "file_path": "test.py",
                "old_string": "this does not exist",
                "new_string": "replacement",
            }
        )

        assert result.success is False
        assert "not found" in result.error.lower()
        edit_tool._docker_runtime.write_file.assert_not_awaited()

    async def test_edit_old_string_ambiguous(self, edit_tool):
        edit_tool._docker_runtime.read_file.return_value = b"repeat\nrepeat\nend\n"

        result = await edit_tool.execute_context(
            {"file_path": "ambiguous.py", "old_string": "repeat", "new_string": "changed"}
        )

        assert result.success is False
        assert "ambiguous" in result.error.lower()
        edit_tool._docker_runtime.write_file.assert_not_awaited()

    async def test_edit_file_not_found(self, edit_tool):
        edit_tool._docker_runtime.read_file.side_effect = FileNotFoundError("not found")

        result = await edit_tool.execute_context(
            {"file_path": "missing.py", "old_string": "old", "new_string": "new"}
        )

        assert result.success is False
        assert "not found" in result.error.lower()

    async def test_edit_runtime_not_initialized(self, edit_tool):
        edit_tool._docker_runtime = None

        result = await edit_tool.execute_context(
            {"file_path": "test.py", "old_string": "old", "new_string": "new"}
        )

        assert result.success is False
        assert "not initialized" in result.error.lower()


class TestListDirectoryTool:
    """file-list unpacks the directory's get_archive tar host-side
    (issue #40) — the output contract (entry fields, dir-first sorting,
    truncation) is unchanged, and no command runs in the container."""

    @pytest.fixture
    def list_tool(self):
        tool = _projection("file-list")
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()
        return tool

    async def test_list_success(self, list_tool):
        list_tool._docker_runtime.fetch_archive.return_value = _make_dir_tar(
            [
                ("workspace", None, _epoch("2026-09-01T00:00")),
                ("workspace/src", None, _epoch("2026-09-05T14:32")),
                ("workspace/README.md", b"x" * 123, _epoch("2026-09-04T09:10")),
                ("workspace/link", "README.md", _epoch("2026-09-03T08:00")),
            ]
        )

        result = await list_tool.execute_context({"path": "."})

        assert result.success is True
        assert result.data["count"] == 3
        assert result.data["truncated"] is False
        # Directories sort first, then files by name
        assert [e["name"] for e in result.data["entries"]] == ["src", "README.md", "link"]
        assert result.data["entries"][0]["type"] == "dir"
        assert result.data["entries"][1] == {
            "name": "README.md",
            "type": "file",
            "size": 123,
            "modified": "2026-09-04T09:10",
        }
        assert result.data["entries"][2]["type"] == "symlink"

        list_tool._docker_runtime.fetch_archive.assert_awaited_once_with("/workspace")
        list_tool._docker_runtime.run.assert_not_awaited()

    async def test_list_truncation(self, list_tool):
        members = [("workspace", None, 0.0)] + [
            (f"workspace/file{i}.txt", b"x", _epoch("2026-09-05T00:00")) for i in range(6)
        ]
        list_tool._docker_runtime.fetch_archive.return_value = _make_dir_tar(members)

        result = await list_tool.execute_context({"path": ".", "max_entries": 5})

        assert result.success is True
        assert result.data["count"] == 5
        assert result.data["truncated"] is True
        assert "hint" in result.data

    async def test_list_not_found(self, list_tool):
        list_tool._docker_runtime.fetch_archive.side_effect = FileNotFoundError(
            "Path not found in container: /workspace/missing"
        )

        result = await list_tool.execute_context({"path": "missing"})

        assert result.success is False
        assert "not found" in result.error.lower()

    async def test_list_not_a_directory(self, list_tool):
        # get_archive on a plain file returns a single-file archive
        list_tool._docker_runtime.fetch_archive.return_value = _make_dir_tar(
            [("README.md", b"content", _epoch("2026-09-04T09:10"))]
        )

        result = await list_tool.execute_context({"path": "README.md"})

        assert result.success is False
        assert "not a directory" in result.error.lower()

    async def test_list_runtime_not_initialized(self, list_tool):
        list_tool._docker_runtime = None

        result = await list_tool.execute_context({"path": "."})

        assert result.success is False
        assert "not initialized" in result.error.lower()


class TestSearchFilesTool:
    """file-search fetches the search root's get_archive tar and matches
    host-side (issue #40): filename mode = case-insensitive glob on
    basenames (the old find -iname), content mode = regex with line
    numbers (the old grep -rnIsE). The output contract (match fields,
    caps, truncation, .git exclusion) is unchanged; no command runs in
    the container."""

    @pytest.fixture
    def search_tool(self):
        tool = _projection("file-search")
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()
        return tool

    async def test_search_content_success(self, search_tool):
        main_py = ("\n" * 11) + "def main():\n"  # match at line 12
        search_tool._docker_runtime.fetch_archive.return_value = _make_dir_tar(
            [
                ("src", None, 0.0),
                ("src/main.py", main_py.encode(), 0.0),
                ("src/README.md", b"intro\nmain entry\n", 0.0),
            ]
        )

        result = await search_tool.execute_context({"pattern": "main", "path": "src"})

        assert result.success is True
        assert result.data["count"] == 2
        assert result.data["truncated"] is False
        assert result.data["matches"][0] == {
            "file": "/workspace/src/main.py",
            "line": 12,
            "text": "def main():",
        }
        assert result.data["matches"][1] == {
            "file": "/workspace/src/README.md",
            "line": 2,
            "text": "main entry",
        }

        search_tool._docker_runtime.fetch_archive.assert_awaited_once_with("/workspace/src")
        search_tool._docker_runtime.run.assert_not_awaited()

    async def test_search_content_skips_binary_and_dot_git(self, search_tool):
        search_tool._docker_runtime.fetch_archive.return_value = _make_dir_tar(
            [
                ("src", None, 0.0),
                ("src/blob.bin", b"\x00\x01main\x00", 0.0),
                ("src/.git", None, 0.0),
                ("src/.git/config", b"main\n", 0.0),
                ("src/ok.txt", b"main\n", 0.0),
            ]
        )

        result = await search_tool.execute_context({"pattern": "main", "path": "src"})

        assert result.success is True
        assert [m["file"] for m in result.data["matches"]] == ["/workspace/src/ok.txt"]

    async def test_search_content_file_root(self, search_tool):
        """grep -r accepted a plain file as root; the archive path keeps that."""
        search_tool._docker_runtime.fetch_archive.return_value = _make_dir_tar(
            [("main.py", b"def main():\n", 0.0)]
        )

        result = await search_tool.execute_context({"pattern": "main", "path": "src/main.py"})

        assert result.success is True
        assert result.data["matches"] == [
            {"file": "/workspace/src/main.py", "line": 1, "text": "def main():"}
        ]

    async def test_search_content_line_truncation(self, search_tool):
        long_line = "x" * 500
        search_tool._docker_runtime.fetch_archive.return_value = _make_dir_tar(
            [("big.js", long_line.encode() + b"\n", 0.0)]
        )

        result = await search_tool.execute_context({"pattern": "x+", "path": "big.js"})

        assert result.success is True
        text = result.data["matches"][0]["text"]
        assert len(text) == MemberFilesystemService._MAX_LINE_CHARS + 1
        assert text.endswith("…")

    async def test_search_content_truncation(self, search_tool):
        content = b"".join(b"match\n" for _ in range(6))
        search_tool._docker_runtime.fetch_archive.return_value = _make_dir_tar(
            [("f.py", content, 0.0)]
        )

        result = await search_tool.execute_context(
            {"pattern": "match", "path": "f.py", "max_results": 5}
        )

        assert result.success is True
        assert result.data["count"] == 5
        assert result.data["truncated"] is True
        assert "hint" in result.data

    async def test_search_no_matches(self, search_tool):
        search_tool._docker_runtime.fetch_archive.return_value = _make_dir_tar(
            [("src", None, 0.0), ("src/a.txt", b"hello\n", 0.0)]
        )

        result = await search_tool.execute_context({"pattern": "nothing_here", "path": "src"})

        assert result.success is True
        assert result.data["count"] == 0
        assert result.data["matches"] == []

    async def test_search_filename_mode(self, search_tool):
        search_tool._docker_runtime.fetch_archive.return_value = _make_dir_tar(
            [
                ("workspace", None, 0.0),
                ("workspace/src", None, 0.0),
                ("workspace/src/main.py", b"", 0.0),
                ("workspace/src/tests", None, 0.0),
                ("workspace/src/tests/test_main.py", b"", 0.0),
                ("workspace/.git", None, 0.0),
                ("workspace/.git/hooks", None, 0.0),
                ("workspace/.git/hooks/x.py", b"", 0.0),
            ]
        )

        result = await search_tool.execute_context({"pattern": "*.py", "mode": "filename"})

        assert result.success is True
        assert result.data["matches"] == [
            "/workspace/src/main.py",
            "/workspace/src/tests/test_main.py",
        ]
        search_tool._docker_runtime.run.assert_not_awaited()

    async def test_search_filename_is_case_insensitive(self, search_tool):
        search_tool._docker_runtime.fetch_archive.return_value = _make_dir_tar(
            [("workspace", None, 0.0), ("workspace/Main.PY", b"", 0.0)]
        )

        result = await search_tool.execute_context({"pattern": "*.py", "mode": "filename"})

        assert result.data["matches"] == ["/workspace/Main.PY"]

    async def test_search_filename_requires_directory_root(self, search_tool):
        search_tool._docker_runtime.fetch_archive.return_value = _make_dir_tar(
            [("main.py", b"", 0.0)]
        )

        result = await search_tool.execute_context(
            {"pattern": "*.py", "mode": "filename", "path": "src/main.py"}
        )

        assert result.success is False
        assert "not found" in result.error.lower()

    async def test_search_invalid_mode(self, search_tool):
        result = await search_tool.execute_context({"pattern": "x", "mode": "sideways"})

        assert result.success is False
        assert "content" in result.error.lower()
        search_tool._docker_runtime.fetch_archive.assert_not_awaited()

    async def test_search_bad_regex(self, search_tool):
        result = await search_tool.execute_context({"pattern": "(["})

        assert result.success is False
        assert "search failed" in result.error.lower()
        search_tool._docker_runtime.fetch_archive.assert_not_awaited()

    async def test_search_path_not_found(self, search_tool):
        search_tool._docker_runtime.fetch_archive.side_effect = FileNotFoundError(
            "Path not found in container: /workspace/missing"
        )

        result = await search_tool.execute_context({"pattern": "x", "path": "missing"})

        assert result.success is False
        assert "not found" in result.error.lower()

    async def test_search_runtime_not_initialized(self, search_tool):
        search_tool._docker_runtime = None

        result = await search_tool.execute_context({"pattern": "x"})

        assert result.success is False
        assert "not initialized" in result.error.lower()


class TestPathGuard:
    """Unit tests for container-path normalization and whitelist checks."""

    def test_normalize_collapses_dot_and_duplicate_separators(self):
        assert normalize_container_path("/workspace//a/./b/") == "/workspace/a/b"

    def test_normalize_resolves_dotdot(self):
        assert normalize_container_path("/workspace/a/../b") == "/workspace/b"

    def test_normalize_clamps_dotdot_at_root(self):
        assert normalize_container_path("/../etc") == "/etc"

    def test_normalize_collapses_leading_double_slash(self):
        assert normalize_container_path("//workspace/a") == "/workspace/a"

    def test_resolve_valid_relative_path(self):
        resolved = resolve_container_path("src/main.py", "/workspace", ["/workspace"])
        assert resolved == "/workspace/src/main.py"

    def test_resolve_valid_relative_path_with_dotdot_inside_sandbox(self):
        resolved = resolve_container_path("a/../b.txt", "/workspace", ["/workspace"])
        assert resolved == "/workspace/b.txt"

    def test_resolve_valid_absolute_path_in_whitelist(self):
        resolved = resolve_container_path("/workspace/notes/x.md", "/workspace", ["/workspace"])
        assert resolved == "/workspace/notes/x.md"

    def test_resolve_absolute_path_outside_whitelist_rejected(self):
        with pytest.raises(ValueError, match="outside the allowed paths"):
            resolve_container_path("/etc/passwd", "/workspace", ["/workspace"])

    def test_resolve_dotdot_escape_rejected(self):
        with pytest.raises(ValueError, match="outside the allowed paths"):
            resolve_container_path("../outside.txt", "/workspace", ["/workspace"])

    def test_resolve_deep_dotdot_escape_rejected(self):
        with pytest.raises(ValueError, match="outside the allowed paths"):
            resolve_container_path("a/../../etc/passwd", "/workspace", ["/workspace"])

    def test_resolve_sibling_prefix_not_allowed(self):
        # "/workspace2" shares a string prefix with "/workspace" but is outside
        with pytest.raises(ValueError, match="outside the allowed paths"):
            resolve_container_path("/workspace2/a.txt", "/workspace", ["/workspace"])

    def test_resolve_custom_allowed_paths(self):
        resolved = resolve_container_path(
            "/data/shared/f.txt", "/workspace", ["/workspace", "/data/shared"]
        )
        assert resolved == "/data/shared/f.txt"

    def test_resolve_custom_allowed_paths_still_rejects_others(self):
        with pytest.raises(ValueError, match="outside the allowed paths"):
            resolve_container_path("/root/.ssh/id_rsa", "/workspace", ["/workspace", "/data"])

    def test_resolve_allowed_root_itself(self):
        resolved = resolve_container_path("/workspace/", "/workspace", ["/workspace"])
        assert resolved == "/workspace"

    def test_resolve_empty_path_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            resolve_container_path("", "/workspace", ["/workspace"])


class TestFilesystemToolSandbox:
    """Tools must reject paths outside the whitelist before touching Docker."""

    async def test_read_rejects_absolute_path_outside_whitelist(self):
        tool = _projection("file-read")
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()

        result = await tool.execute_context({"file_path": "/etc/passwd"})

        assert result.success is False
        assert "outside the allowed paths" in result.error
        tool._docker_runtime.read_file.assert_not_awaited()

    async def test_read_rejects_dotdot_escape(self):
        tool = _projection("file-read")
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()

        result = await tool.execute_context({"file_path": "../../etc/shadow"})

        assert result.success is False
        assert "outside the allowed paths" in result.error
        tool._docker_runtime.read_file.assert_not_awaited()

    async def test_write_rejects_absolute_path_outside_whitelist(self):
        tool = _projection("file-write")
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()

        result = await tool.execute_context({"file_path": "/usr/bin/evil", "content": "x"})

        assert result.success is False
        assert "outside the allowed paths" in result.error
        tool._docker_runtime.write_file.assert_not_awaited()

    async def test_edit_rejects_dotdot_escape(self):
        tool = _projection("file-edit")
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()

        result = await tool.execute_context(
            {"file_path": "../secret.txt", "old_string": "a", "new_string": "b"}
        )

        assert result.success is False
        assert "outside the allowed paths" in result.error
        tool._docker_runtime.read_file.assert_not_awaited()

    async def test_list_rejects_absolute_path_outside_whitelist(self):
        tool = _projection("file-list")
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()

        result = await tool.execute_context({"path": "/etc"})

        assert result.success is False
        assert "outside the allowed paths" in result.error
        tool._docker_runtime.fetch_archive.assert_not_awaited()

    async def test_search_rejects_dotdot_escape(self):
        tool = _projection("file-search")
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()

        result = await tool.execute_context({"pattern": "x", "path": "../../etc"})

        assert result.success is False
        assert "outside the allowed paths" in result.error
        tool._docker_runtime.fetch_archive.assert_not_awaited()

    async def test_read_allows_absolute_path_in_whitelist(self):
        tool = _projection("file-read")
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()
        tool._docker_runtime.read_file.return_value = b"hello\n"

        result = await tool.execute_context({"file_path": "/workspace//a/./b.txt"})

        assert result.success is True
        tool._docker_runtime.read_file.assert_awaited_once_with("/workspace/a/b.txt")

    async def test_write_honors_custom_allowed_paths(self):
        tool = _projection("file-write")
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace", "/data/shared"]
        tool._docker_runtime = _make_mock_runtime()

        ok = await tool.execute_context({"file_path": "/data/shared/out.txt", "content": "x"})
        assert ok.success is True
        tool._docker_runtime.write_file.assert_awaited_once_with("/data/shared/out.txt", b"x")

        rejected = await tool.execute_context({"file_path": "/etc/cron.d/job", "content": "x"})
        assert rejected.success is False
        assert "outside the allowed paths" in rejected.error

    def test_tool_constructor_defaults_allowed_paths(self):
        tool = _projection("file-read", MagicMock(working_dir="/workspace"), None)
        assert tool._allowed_paths == ["/workspace"]


class TestAgentHomeAccess:
    """Agent home read access (docs/reference/tools-reference.md): the
    agent's own home is mounted
    read-only at /tlon-home; read/list/search whitelist it, write/edit don't."""

    _READ_PATHS = ["/workspace", "/tlon-home"]
    _WRITE_PATHS = ["/workspace"]

    def _make(self, cls, allowed_paths):
        tool = _projection(cls)
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = allowed_paths
        tool._docker_runtime = _make_mock_runtime()
        return tool

    async def test_read_own_home_allowed(self):
        tool = self._make("file-read", self._READ_PATHS)
        tool._docker_runtime.read_file.return_value = b":rules []\n"

        result = await tool.execute_context({"file_path": "/tlon-home/config/behavior.tlon"})

        assert result.success is True
        tool._docker_runtime.read_file.assert_awaited_once_with("/tlon-home/config/behavior.tlon")

    async def test_read_own_mailbox_allowed(self):
        tool = self._make("file-read", self._READ_PATHS)
        tool._docker_runtime.read_file.return_value = b"{}\n"

        result = await tool.execute_context({"file_path": "/tlon-home/mail/inbox.jsonl"})

        assert result.success is True

    async def test_list_own_home_allowed(self):
        tool = self._make("file-list", self._READ_PATHS)
        tool._docker_runtime.fetch_archive.return_value = _make_dir_tar(
            [("tlon-home", None, 0.0), ("tlon-home/config", None, _epoch("2026-09-05T14:32"))]
        )

        result = await tool.execute_context({"path": "/tlon-home"})

        assert result.success is True
        tool._docker_runtime.fetch_archive.assert_awaited_once_with("/tlon-home")

    async def test_search_own_home_allowed(self):
        tool = self._make("file-search", self._READ_PATHS)
        tool._docker_runtime.fetch_archive.return_value = _make_dir_tar(
            [
                ("config", None, 0.0),
                ("config/behavior.tlon", b"(defrule x)\n", 0.0),
            ]
        )

        result = await tool.execute_context({"pattern": "defrule", "path": "/tlon-home/config"})

        assert result.success is True
        assert result.data["matches"][0]["file"] == "/tlon-home/config/behavior.tlon"

    async def test_write_own_home_rejected(self):
        tool = self._make("file-write", self._WRITE_PATHS)

        result = await tool.execute_context(
            {"file_path": "/tlon-home/config/behavior.tlon", "content": "x"}
        )

        assert result.success is False
        assert "read-only" in result.error
        tool._docker_runtime.write_file.assert_not_awaited()

    async def test_edit_own_home_rejected(self):
        tool = self._make("file-edit", self._WRITE_PATHS)

        result = await tool.execute_context(
            {"file_path": "/tlon-home/memory/config.json", "old_string": "a", "new_string": "b"}
        )

        assert result.success is False
        assert "read-only" in result.error
        tool._docker_runtime.read_file.assert_not_awaited()

    async def test_read_outside_workspace_and_home_rejected(self):
        # Cross-agent reads are structurally impossible (only the agent's own
        # home is mounted into its container); the whitelist rejects anything
        # outside /workspace and /tlon-home, sibling prefixes included.
        tool = self._make("file-read", self._READ_PATHS)

        for path in ("/etc/passwd", "/tlon-home-other/config/agent.yaml"):
            result = await tool.execute_context({"file_path": path})

            assert result.success is False
            assert "outside the allowed paths" in result.error
        tool._docker_runtime.read_file.assert_not_awaited()


class TestToolConfigAllowedPaths:
    def test_default_allowed_paths(self):
        config = ToolConfig()
        assert config.allowed_paths == ["/workspace"]

    def test_custom_allowed_paths(self):
        config = ToolConfig(allowed_paths=["/workspace", "/data"])
        assert config.allowed_paths == ["/workspace", "/data"]

    def test_rejects_empty_allowed_paths(self):
        with pytest.raises(ValidationError):
            ToolConfig(allowed_paths=[])

    def test_rejects_relative_allowed_paths(self):
        with pytest.raises(ValidationError):
            ToolConfig(allowed_paths=["workspace"])
