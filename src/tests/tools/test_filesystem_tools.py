"""Tests for filesystem tools (ReadFile, WriteFile, EditFile) via DockerRuntime."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tlon.base.config import ToolConfig
from tlon.tools.filesystem.edit_file import EditFileTool
from tlon.tools.filesystem.path_guard import normalize_container_path, resolve_container_path
from tlon.tools.filesystem.read_file import ReadFileTool
from tlon.tools.filesystem.write_file import WriteFileTool


def _make_mock_runtime():
    """Create a mock DockerRuntime with async file I/O methods."""
    runtime = MagicMock()
    runtime.read_file = AsyncMock()
    runtime.write_file = AsyncMock(return_value=True)
    runtime.file_exists = AsyncMock(return_value=False)
    runtime.execute = AsyncMock(return_value=MagicMock(exit_code=0, stdout="", stderr=""))
    return runtime


class TestReadFileTool:
    @pytest.fixture
    def read_tool(self):
        tool = ReadFileTool.__new__(ReadFileTool)
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()
        return tool

    async def test_read_full_file(self, read_tool):
        content = "Line 1\nLine 2\nLine 3\n"
        read_tool._docker_runtime.read_file.return_value = content.encode("utf-8")

        result = await read_tool.execute(file_path="test.txt")

        assert result.success is True
        assert result.data["file_path"] == "test.txt"
        assert result.data["total_lines"] == 3
        assert result.data["read_lines"] == 3
        assert "Line 1" in result.data["content"]

        read_tool._docker_runtime.read_file.assert_awaited_once_with("/workspace/test.txt")

    async def test_read_with_offset_and_limit(self, read_tool):
        content = "\n".join([f"Line {i}" for i in range(1, 21)]) + "\n"
        read_tool._docker_runtime.read_file.return_value = content.encode("utf-8")

        result = await read_tool.execute(file_path="test.txt", offset=5, limit=3)

        assert result.success is True
        assert result.data["read_lines"] == 3
        assert result.data["offset"] == 5
        assert "Line 5" in result.data["content"]
        assert "Line 7" in result.data["content"]
        assert "Line 4" not in result.data["content"]

    async def test_read_with_limit_truncation(self, read_tool):
        lines = [f"Line {i}" for i in range(1, 201)]
        read_tool._docker_runtime.read_file.return_value = ("\n".join(lines)).encode("utf-8")

        result = await read_tool.execute(file_path="test.txt", limit=50)

        assert result.success is True
        assert result.data["read_lines"] == 50
        assert "... (150 more lines)" in result.data["content"]

    async def test_read_max_limit_enforced(self, read_tool):
        lines = [f"Line {i}" for i in range(1, 600)]
        read_tool._docker_runtime.read_file.return_value = ("\n".join(lines)).encode("utf-8")

        result = await read_tool.execute(file_path="test.txt", limit=1000)

        assert result.success is True
        assert result.data["read_lines"] == 500  # MAX_LIMIT

    async def test_read_file_not_found(self, read_tool):
        read_tool._docker_runtime.read_file.side_effect = FileNotFoundError("not found")

        result = await read_tool.execute(file_path="missing.txt")

        assert result.success is False
        assert "not found" in result.error.lower()

    async def test_read_runtime_error(self, read_tool):
        read_tool._docker_runtime.read_file.side_effect = RuntimeError("container crashed")

        result = await read_tool.execute(file_path="test.txt")

        assert result.success is False
        assert "container crashed" in result.error.lower()

    async def test_read_runtime_not_initialized(self, read_tool):
        read_tool._docker_runtime = None

        result = await read_tool.execute(file_path="test.txt")

        assert result.success is False
        assert "not initialized" in result.error.lower()


class TestWriteFileTool:
    @pytest.fixture
    def write_tool(self):
        tool = WriteFileTool.__new__(WriteFileTool)
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()
        return tool

    async def test_write_new_file(self, write_tool):
        write_tool._docker_runtime.file_exists.return_value = False

        result = await write_tool.execute(
            file_path="new_file.txt",
            content="Hello, world!",
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
        write_tool._docker_runtime.file_exists.return_value = False

        result = await write_tool.execute(
            file_path="deep/nested/file.txt",
            content="nested",
        )

        assert result.success is True
        write_tool._docker_runtime.execute.assert_awaited_once()
        call_args = write_tool._docker_runtime.execute.await_args[0][0]
        assert "mkdir -p" in call_args

    async def test_overwrite_existing_file(self, write_tool):
        write_tool._docker_runtime.file_exists.return_value = True

        result = await write_tool.execute(
            file_path="existing.txt",
            content="new content",
        )

        assert result.success is True
        assert result.data["existed_before"] is True

    async def test_write_runtime_not_initialized(self, write_tool):
        write_tool._docker_runtime = None

        result = await write_tool.execute(file_path="test.txt", content="x")

        assert result.success is False
        assert "not initialized" in result.error.lower()


class TestEditFileTool:
    @pytest.fixture
    def edit_tool(self):
        tool = EditFileTool.__new__(EditFileTool)
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()
        return tool

    async def test_edit_success(self, edit_tool):
        original = "def old_func():\n    return 1\n\ndef another():\n    pass\n"
        edit_tool._docker_runtime.read_file.return_value = original.encode("utf-8")

        result = await edit_tool.execute(
            file_path="editable.py",
            old_string="def old_func():\n    return 1",
            new_string="def new_func():\n    return 42",
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

        result = await edit_tool.execute(
            file_path="test.py",
            old_string="this does not exist",
            new_string="replacement",
        )

        assert result.success is False
        assert "not found" in result.error.lower()
        edit_tool._docker_runtime.write_file.assert_not_awaited()

    async def test_edit_old_string_ambiguous(self, edit_tool):
        edit_tool._docker_runtime.read_file.return_value = b"repeat\nrepeat\nend\n"

        result = await edit_tool.execute(
            file_path="ambiguous.py",
            old_string="repeat",
            new_string="changed",
        )

        assert result.success is False
        assert "ambiguous" in result.error.lower()
        edit_tool._docker_runtime.write_file.assert_not_awaited()

    async def test_edit_file_not_found(self, edit_tool):
        edit_tool._docker_runtime.read_file.side_effect = FileNotFoundError("not found")

        result = await edit_tool.execute(
            file_path="missing.py",
            old_string="old",
            new_string="new",
        )

        assert result.success is False
        assert "not found" in result.error.lower()

    async def test_edit_runtime_not_initialized(self, edit_tool):
        edit_tool._docker_runtime = None

        result = await edit_tool.execute(
            file_path="test.py",
            old_string="old",
            new_string="new",
        )

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
        tool = ReadFileTool.__new__(ReadFileTool)
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()

        result = await tool.execute(file_path="/etc/passwd")

        assert result.success is False
        assert "outside the allowed paths" in result.error
        tool._docker_runtime.read_file.assert_not_awaited()

    async def test_read_rejects_dotdot_escape(self):
        tool = ReadFileTool.__new__(ReadFileTool)
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()

        result = await tool.execute(file_path="../../etc/shadow")

        assert result.success is False
        assert "outside the allowed paths" in result.error
        tool._docker_runtime.read_file.assert_not_awaited()

    async def test_write_rejects_absolute_path_outside_whitelist(self):
        tool = WriteFileTool.__new__(WriteFileTool)
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()

        result = await tool.execute(file_path="/usr/bin/evil", content="x")

        assert result.success is False
        assert "outside the allowed paths" in result.error
        tool._docker_runtime.write_file.assert_not_awaited()

    async def test_edit_rejects_dotdot_escape(self):
        tool = EditFileTool.__new__(EditFileTool)
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()

        result = await tool.execute(file_path="../secret.txt", old_string="a", new_string="b")

        assert result.success is False
        assert "outside the allowed paths" in result.error
        tool._docker_runtime.read_file.assert_not_awaited()

    async def test_read_allows_absolute_path_in_whitelist(self):
        tool = ReadFileTool.__new__(ReadFileTool)
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()
        tool._docker_runtime.read_file.return_value = b"hello\n"

        result = await tool.execute(file_path="/workspace//a/./b.txt")

        assert result.success is True
        tool._docker_runtime.read_file.assert_awaited_once_with("/workspace/a/b.txt")

    async def test_write_honors_custom_allowed_paths(self):
        tool = WriteFileTool.__new__(WriteFileTool)
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace", "/data/shared"]
        tool._docker_runtime = _make_mock_runtime()

        ok = await tool.execute(file_path="/data/shared/out.txt", content="x")
        assert ok.success is True
        tool._docker_runtime.write_file.assert_awaited_once_with("/data/shared/out.txt", b"x")

        rejected = await tool.execute(file_path="/etc/cron.d/job", content="x")
        assert rejected.success is False
        assert "outside the allowed paths" in rejected.error

    def test_tool_constructor_defaults_allowed_paths(self):
        tool = ReadFileTool(MagicMock(working_dir="/workspace"), None)
        assert tool._allowed_paths == ["/workspace"]


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
