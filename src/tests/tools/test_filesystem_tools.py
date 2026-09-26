"""Tests for filesystem tools (ReadFile, WriteFile, EditFile, ListDirectory,
SearchFiles) via the ExecutionDevice interface."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from tlon.base.config import ToolConfig
from tlon.tools.filesystem.edit_file import EditFileTool
from tlon.tools.filesystem.list_directory import ListDirectoryTool
from tlon.tools.filesystem.path_guard import normalize_container_path, resolve_container_path
from tlon.tools.filesystem.read_file import ReadFileTool
from tlon.tools.filesystem.search_files import SearchFilesTool
from tlon.tools.filesystem.write_file import WriteFileTool


def _make_mock_runtime():
    """Create a mock ExecutionDevice with async file I/O methods."""
    runtime = MagicMock()
    runtime.read_file = AsyncMock()
    runtime.write_file = AsyncMock(return_value=True)
    runtime.file_exists = AsyncMock(return_value=False)
    runtime.run = AsyncMock(return_value=MagicMock(exit_code=0, stdout="", stderr=""))
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
        write_tool._docker_runtime.run.assert_awaited_once()
        call_args = write_tool._docker_runtime.run.await_args[0][0]
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


class TestListDirectoryTool:
    @pytest.fixture
    def list_tool(self):
        tool = ListDirectoryTool.__new__(ListDirectoryTool)
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()
        return tool

    async def test_list_success(self, list_tool):
        list_tool._docker_runtime.run.return_value = MagicMock(
            exit_code=0,
            stdout=(
                "d\t4096\t2026-09-05T14:32\tsrc\n"
                "f\t123\t2026-09-04T09:10\tREADME.md\n"
                "l\t7\t2026-09-03T08:00\tlink\n"
            ),
            stderr="",
        )

        result = await list_tool.execute(path=".")

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

        command = list_tool._docker_runtime.run.await_args[0][0]
        assert "find /workspace" in command

    async def test_list_truncation(self, list_tool):
        lines = [f"f\t1\t2026-09-05T00:00\tfile{i}.txt" for i in range(6)]
        list_tool._docker_runtime.run.return_value = MagicMock(
            exit_code=0, stdout="\n".join(lines) + "\n", stderr=""
        )

        result = await list_tool.execute(path=".", max_entries=5)

        assert result.success is True
        assert result.data["count"] == 5
        assert result.data["truncated"] is True
        assert "hint" in result.data

    async def test_list_not_found(self, list_tool):
        list_tool._docker_runtime.run.return_value = MagicMock(
            exit_code=1, stdout="", stderr="__TLON_NOT_FOUND__\n"
        )

        result = await list_tool.execute(path="missing")

        assert result.success is False
        assert "not found" in result.error.lower()

    async def test_list_not_a_directory(self, list_tool):
        list_tool._docker_runtime.run.return_value = MagicMock(
            exit_code=1, stdout="", stderr="__TLON_NOT_A_DIRECTORY__\n"
        )

        result = await list_tool.execute(path="README.md")

        assert result.success is False
        assert "not a directory" in result.error.lower()

    async def test_list_runtime_not_initialized(self, list_tool):
        list_tool._docker_runtime = None

        result = await list_tool.execute(path=".")

        assert result.success is False
        assert "not initialized" in result.error.lower()


class TestSearchFilesTool:
    @pytest.fixture
    def search_tool(self):
        tool = SearchFilesTool.__new__(SearchFilesTool)
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()
        return tool

    async def test_search_content_success(self, search_tool):
        search_tool._docker_runtime.run.return_value = MagicMock(
            exit_code=0,
            stdout=(
                "/workspace/src/main.py\x0012:def main():\n/workspace/README.md\x003:main entry\n"
            ),
            stderr="",
        )

        result = await search_tool.execute(pattern="main", path="src")

        assert result.success is True
        assert result.data["count"] == 2
        assert result.data["truncated"] is False
        assert result.data["matches"][0] == {
            "file": "/workspace/src/main.py",
            "line": 12,
            "text": "def main():",
        }
        assert result.data["matches"][1]["file"] == "/workspace/README.md"

        command = search_tool._docker_runtime.run.await_args[0][0]
        assert "grep" in command
        assert "/workspace/src" in command

    async def test_search_content_line_truncation(self, search_tool):
        long_line = "x" * 500
        search_tool._docker_runtime.run.return_value = MagicMock(
            exit_code=0,
            stdout=f"/workspace/big.js\x001:{long_line}\n",
            stderr="",
        )

        result = await search_tool.execute(pattern="x+")

        assert result.success is True
        text = result.data["matches"][0]["text"]
        assert len(text) == SearchFilesTool._MAX_LINE_CHARS + 1
        assert text.endswith("…")

    async def test_search_content_truncation(self, search_tool):
        lines = [f"/workspace/f.py\x00{i}:match" for i in range(6)]
        search_tool._docker_runtime.run.return_value = MagicMock(
            exit_code=0, stdout="\n".join(lines) + "\n", stderr=""
        )

        result = await search_tool.execute(pattern="match", max_results=5)

        assert result.success is True
        assert result.data["count"] == 5
        assert result.data["truncated"] is True
        assert "hint" in result.data

    async def test_search_no_matches(self, search_tool):
        search_tool._docker_runtime.run.return_value = MagicMock(
            exit_code=0, stdout="", stderr=""
        )

        result = await search_tool.execute(pattern="nothing_here")

        assert result.success is True
        assert result.data["count"] == 0
        assert result.data["matches"] == []

    async def test_search_filename_mode(self, search_tool):
        search_tool._docker_runtime.run.return_value = MagicMock(
            exit_code=0,
            stdout="/workspace/src/main.py\n/workspace/src/tests/test_main.py\n",
            stderr="",
        )

        result = await search_tool.execute(pattern="*.py", mode="filename")

        assert result.success is True
        assert result.data["matches"] == [
            "/workspace/src/main.py",
            "/workspace/src/tests/test_main.py",
        ]
        command = search_tool._docker_runtime.run.await_args[0][0]
        assert "find /workspace -iname" in command

    async def test_search_invalid_mode(self, search_tool):
        result = await search_tool.execute(pattern="x", mode="sideways")

        assert result.success is False
        assert "invalid mode" in result.error.lower()
        search_tool._docker_runtime.run.assert_not_awaited()

    async def test_search_bad_regex(self, search_tool):
        search_tool._docker_runtime.run.return_value = MagicMock(
            exit_code=0, stdout="", stderr="grep: Invalid regular expression\n"
        )

        result = await search_tool.execute(pattern="([")

        assert result.success is False
        assert "search failed" in result.error.lower()

    async def test_search_path_not_found(self, search_tool):
        search_tool._docker_runtime.run.return_value = MagicMock(
            exit_code=1, stdout="", stderr="__TLON_NOT_FOUND__\n"
        )

        result = await search_tool.execute(pattern="x", path="missing")

        assert result.success is False
        assert "not found" in result.error.lower()

    async def test_search_runtime_not_initialized(self, search_tool):
        search_tool._docker_runtime = None

        result = await search_tool.execute(pattern="x")

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

    async def test_list_rejects_absolute_path_outside_whitelist(self):
        tool = ListDirectoryTool.__new__(ListDirectoryTool)
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()

        result = await tool.execute(path="/etc")

        assert result.success is False
        assert "outside the allowed paths" in result.error
        tool._docker_runtime.run.assert_not_awaited()

    async def test_search_rejects_dotdot_escape(self):
        tool = SearchFilesTool.__new__(SearchFilesTool)
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = ["/workspace"]
        tool._docker_runtime = _make_mock_runtime()

        result = await tool.execute(pattern="x", path="../../etc")

        assert result.success is False
        assert "outside the allowed paths" in result.error
        tool._docker_runtime.run.assert_not_awaited()

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


class TestAgentHomeAccess:
    """Agent home read access (issue ): the agent's own home is mounted
    read-only at /tlon-home; read/list/search whitelist it, write/edit don't."""

    _READ_PATHS = ["/workspace", "/tlon-home"]
    _WRITE_PATHS = ["/workspace"]

    def _make(self, cls, allowed_paths):
        tool = cls.__new__(cls)
        tool._docker_config = MagicMock(working_dir="/workspace")
        tool._allowed_paths = allowed_paths
        tool._docker_runtime = _make_mock_runtime()
        return tool

    async def test_read_own_home_allowed(self):
        tool = self._make(ReadFileTool, self._READ_PATHS)
        tool._docker_runtime.read_file.return_value = b":rules []\n"

        result = await tool.execute(file_path="/tlon-home/config/behavior.tlon")

        assert result.success is True
        tool._docker_runtime.read_file.assert_awaited_once_with(
            "/tlon-home/config/behavior.tlon"
        )

    async def test_read_own_mailbox_allowed(self):
        tool = self._make(ReadFileTool, self._READ_PATHS)
        tool._docker_runtime.read_file.return_value = b"{}\n"

        result = await tool.execute(file_path="/tlon-home/mail/inbox.jsonl")

        assert result.success is True

    async def test_list_own_home_allowed(self):
        tool = self._make(ListDirectoryTool, self._READ_PATHS)
        tool._docker_runtime.run.return_value = MagicMock(
            exit_code=0, stdout="d\t4096\t2026-09-05T14:32\tconfig\n", stderr=""
        )

        result = await tool.execute(path="/tlon-home")

        assert result.success is True
        command = tool._docker_runtime.run.await_args[0][0]
        assert "find /tlon-home" in command

    async def test_search_own_home_allowed(self):
        tool = self._make(SearchFilesTool, self._READ_PATHS)
        tool._docker_runtime.run.return_value = MagicMock(
            exit_code=0,
            stdout="/tlon-home/config/behavior.tlon\x001:(defrule x)\n",
            stderr="",
        )

        result = await tool.execute(pattern="defrule", path="/tlon-home/config")

        assert result.success is True
        assert result.data["matches"][0]["file"] == "/tlon-home/config/behavior.tlon"

    async def test_write_own_home_rejected(self):
        tool = self._make(WriteFileTool, self._WRITE_PATHS)

        result = await tool.execute(
            file_path="/tlon-home/config/behavior.tlon", content="x"
        )

        assert result.success is False
        assert "outside the allowed paths" in result.error
        tool._docker_runtime.write_file.assert_not_awaited()

    async def test_edit_own_home_rejected(self):
        tool = self._make(EditFileTool, self._WRITE_PATHS)

        result = await tool.execute(
            file_path="/tlon-home/memory/config.json", old_string="a", new_string="b"
        )

        assert result.success is False
        assert "outside the allowed paths" in result.error
        tool._docker_runtime.read_file.assert_not_awaited()

    async def test_read_outside_workspace_and_home_rejected(self):
        # Cross-agent reads are structurally impossible (only the agent's own
        # home is mounted into its container); the whitelist rejects anything
        # outside /workspace and /tlon-home, sibling prefixes included.
        tool = self._make(ReadFileTool, self._READ_PATHS)

        for path in ("/etc/passwd", "/tlon-home-other/config/agent.yaml"):
            result = await tool.execute(file_path=path)

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
