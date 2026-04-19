"""Tests for filesystem tools (ReadFile, WriteFile, EditFile) via DockerRuntime."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.tools.filesystem.edit_file import EditFileTool
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
        tool._agent_id = "test-agent"
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
        tool._agent_id = "test-agent"
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
        tool._agent_id = "test-agent"
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
