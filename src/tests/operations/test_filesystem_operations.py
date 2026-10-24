"""Shared file contracts against an in-memory device and genuine tar boundaries."""

import io
import tarfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from docker.errors import NotFound

from tlon.base.config import DockerRuntimeConfig
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.filesystem import make_filesystem_operations
from tlon.operations.schema import UnknownOutcomeError
from tlon.sandbox.archive import DirectoryArchive
from tlon.sandbox.docker_runtime import DockerRuntime
from tlon.sandbox.filesystem import MemberFilesystemService
from tlosure.builtins import BuiltinCall
from tlosure.edn import Keyword


class MemoryDevice:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.fail_after_write = False
        self.writes = 0

    async def check_path(self, path, allowed_paths, *, write=False):
        pass

    async def read_file(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write_file(self, path, content):
        self.files[path] = content
        self.writes += 1
        if self.fail_after_write:
            raise OSError("connection lost after commit")
        return True

    async def file_exists(self, path):
        return path in self.files

    async def fetch_archive(self, path):
        raise AssertionError("not needed")

    async def run(self, program, timeout=60, workdir=None):
        raise AssertionError("L1 file service must never execute code")

    def close(self):
        pass


def bound(level="L1"):
    device = MemoryDevice()
    service = MemberFilesystemService(
        DockerRuntimeConfig(level=level, working_dir="/member-home"),
        device,
        ["/workspace", "/tlon-home"],
        ["/workspace"],
    )
    executor = OperationExecutor(
        OperationRegistry(make_filesystem_operations(service)),
        CallContext("alice", "run", "call-1"),
    )
    return device, executor


async def test_dsl_write_model_read_and_unique_unicode_edit():
    device, executor = bound()
    adapter = DSLAdapter(executor)
    call = BuiltinCall(
        "file-write",
        adapter.builtins()["file-write"],
        [
            {
                Keyword("file_path"): "/workspace/notes/中文.txt",
                Keyword("content"): "第一行\n第二行\n",
            }
        ],
        {},
    )
    await adapter.handle(call)
    result = await OperationTool(executor, "file-read").execute_context(
        {"file_path": "/workspace/notes/中文.txt", "offset": 2, "limit": 1}
    )
    assert result.success and result.data["content"] == "第二行"
    result = await OperationTool(executor, "file-edit").execute_context(
        {"file_path": "/workspace/notes/中文.txt", "old_string": "第二行", "new_string": "完成"}
    )
    assert result.success and device.files["/workspace/notes/中文.txt"].decode().endswith("完成\n")
    result = await OperationTool(executor, "file-edit").execute_context(
        {"file_path": "/workspace/notes/中文.txt", "old_string": "不存在", "new_string": "bad"}
    )
    assert not result.success and device.writes == 2


async def test_write_loss_reports_unknown_with_stable_identity():
    device, executor = bound()
    device.fail_after_write = True
    with pytest.raises(UnknownOutcomeError) as exc:
        await executor.call("file-write", {"file_path": "/workspace/a", "content": "committed"})
    assert exc.value.data == {"outcome": "unknown", "call_id": "call-1", "retry_safe": False}
    assert device.files["/workspace/a"] == b"committed"
    assert device.writes == 1


@pytest.mark.parametrize(
    "level,path", [("L0", "a"), ("L1", "../secret"), ("L1", "/tlon-home/behavior.tlon")]
)
async def test_level_and_write_path_reject_before_effect(level, path):
    device, executor = bound(level)
    result = await OperationTool(executor, "file-write").execute_context(
        {"file_path": path, "content": "bad"}
    )
    assert not result.success and not device.files


def archive_member(name, *, link=None):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo(name)
        if link is not None:
            member.type = tarfile.LNKTYPE
            member.linkname = link
            archive.addfile(member)
        else:
            member.size = 6
            archive.addfile(member, io.BytesIO(b"secret"))
    return buffer.getvalue()


@pytest.mark.parametrize("name", ["root/../../etc/passwd", "/root/secret", "root/../escape"])
def test_archive_rejects_traversal(name):
    with pytest.raises(ValueError, match="Unsafe archive"):
        DirectoryArchive(archive_member(name), "/workspace/root")


def test_archive_hard_link_never_reads_target():
    archive = DirectoryArchive(archive_member("root/link", link="/etc/passwd"), "/workspace/root")
    assert archive.entries[0].is_symlink
    assert archive.read(archive.entries[0]) == b""


@pytest.mark.parametrize("link_path", ["/workspace", "/workspace/a", "/workspace/a/file"])
@pytest.mark.parametrize("write", [False, True])
async def test_docker_guard_rejects_each_symlink_ancestor(link_path, write):
    runtime = DockerRuntime.__new__(DockerRuntime)
    runtime.config = DockerRuntimeConfig()
    runtime.container = MagicMock()
    runtime.ensure_running = AsyncMock()
    probed = []

    def metadata(path):
        probed.append(path)
        return {"mode": 1 << 27, "linkTarget": "/etc"} if path == link_path else {"mode": 1 << 31}

    runtime._path_stat = metadata
    with pytest.raises(ValueError, match="Symbolic links"):
        await runtime.check_path("/workspace/a/file", ["/workspace"], write=write)
    assert probed[-1] == link_path
    runtime.container.exec_run.assert_not_called()


async def test_docker_guard_allows_creation_below_missing_parent():
    runtime = DockerRuntime.__new__(DockerRuntime)
    runtime.config = DockerRuntimeConfig()
    runtime.container = MagicMock()

    def metadata(path):
        if path == "/workspace/new":
            raise NotFound("missing")
        return {"mode": 1 << 31}

    runtime._path_stat = metadata
    await runtime.check_path("/workspace/new/deep/file", ["/workspace"], write=True)
    with pytest.raises(FileNotFoundError):
        await runtime.check_path("/workspace/new/deep/file", ["/workspace"], write=False)


async def test_audited_device_forwards_path_authority():
    from tlon.sandbox.device import AuditedExecutionDevice

    inner = MagicMock()
    inner.check_path = AsyncMock()
    audited = AuditedExecutionDevice(inner, "alice")
    await audited.check_path("/workspace/a", ["/workspace"], write=True)
    inner.check_path.assert_awaited_once_with("/workspace/a", ["/workspace"], write=True)
    inner.run.assert_not_called()


async def test_directory_sorting_precedes_truncation():
    device, executor = bound()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, is_directory in [
            ("workspace", True),
            ("workspace/z.txt", False),
            ("workspace/a-dir", True),
        ]:
            entry = tarfile.TarInfo(name)
            if is_directory:
                entry.type = tarfile.DIRTYPE
            archive.addfile(entry)
    device.fetch_archive = AsyncMock(return_value=buffer.getvalue())
    result = await OperationTool(executor, "file-list").execute_context(
        {"path": "/workspace", "max_entries": 1}
    )
    assert result.success
    assert result.data["entries"][0]["name"] == "a-dir"
    assert result.data["truncated"]


async def test_invalid_archive_is_domain_error():
    from tlon.operations.schema import OperationError

    device, executor = bound()
    device.fetch_archive = AsyncMock(return_value=archive_member("workspace/../../etc/passwd"))
    with pytest.raises(OperationError, match="Unsafe archive"):
        await executor.call("file-list", {"path": "/workspace"})


async def test_search_preserves_existing_per_line_display_limit():
    device, executor = bound()
    payload = b"match " + b"x" * 500
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        root = tarfile.TarInfo("root")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        entry = tarfile.TarInfo("root/large.txt")
        entry.size = len(payload)
        archive.addfile(entry, io.BytesIO(payload))
    device.fetch_archive = AsyncMock(return_value=buffer.getvalue())
    result = await executor.call("file-search", {"path": "/workspace/root", "pattern": "match"})
    assert result["count"] == 1
    assert result["matches"][0]["text"] == payload[:200].decode() + "…"


@pytest.mark.parametrize("error", [ValueError("symlink denied"), FileNotFoundError("missing")])
async def test_device_path_rejection_is_model_correctable(error: Exception) -> None:
    device, executor = bound()
    device.check_path = AsyncMock(side_effect=error)
    result = await OperationTool(executor, "file-read").execute_context(
        {"file_path": "/workspace/a"}
    )
    assert not result.success
    assert result.error == f"RejectedError: {error}"


async def test_path_metadata_infrastructure_failure_propagates() -> None:
    device, executor = bound()
    device.check_path = AsyncMock(side_effect=RuntimeError("metadata service unavailable"))
    with pytest.raises(RuntimeError, match="metadata service unavailable"):
        await OperationTool(executor, "file-read").execute_context({"file_path": "/workspace/a"})


async def test_invalid_archive_propagates_through_tool_projection() -> None:
    from tlon.operations.schema import OperationError

    device, executor = bound()
    device.fetch_archive = AsyncMock(return_value=b"broken archive")
    with pytest.raises(OperationError, match="Invalid sandbox archive"):
        await OperationTool(executor, "file-list").execute_context({"path": "/workspace"})
