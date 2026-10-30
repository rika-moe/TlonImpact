"""The published author materials need no Docker or extra filesystem authority."""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tlon.base.config import DockerRuntimeConfig
from tlon.operations.core import CallContext
from tlon.operations.schema import RejectedError
from tlon.sandbox.author_files import author_mounts
from tlon.sandbox.docker_runtime import DockerRuntime
from tlon.sandbox.filesystem import MemberFilesystemService

CONTEXT = CallContext("author", "run", "read")


def filesystem(level: str = "L0", working_dir: str = "/workspace") -> MemberFilesystemService:
    return MemberFilesystemService(
        DockerRuntimeConfig(level=level, working_dir=working_dir),
        None,
        [working_dir],
        [working_dir],
    )


@pytest.mark.parametrize("working_dir", ["/workspace", "/custom"])
async def test_l0_reads_actual_docs_and_standard_library_without_docker(working_dir: str) -> None:
    service = filesystem(working_dir=working_dir)
    result = await service.read({"file_path": "tlon/docs/README.md"}, CONTEXT)
    assert result["content"] and result["total_lines"] > 0
    result = await service.read({"file_path": "tlon/stdlib/messages.tlon"}, CONTEXT)
    assert "(ns tlon.messages" in result["content"]
    listing = await service.list_directory({"path": "tlon/stdlib"}, CONTEXT)
    assert "messages.tlon" in {entry["name"] for entry in listing["entries"]}
    search = await service.search({"path": "tlon/stdlib", "pattern": "tlon.messages"}, CONTEXT)
    assert search["count"] > 0
    assert search["matches"][0]["file"].startswith(working_dir + "/tlon/stdlib/")


@pytest.mark.parametrize(
    "path", ["/etc/passwd", "/workspace/private", "/workspace/tlon/docs/../../private"]
)
async def test_l0_reference_access_does_not_grant_general_host_reads(path: str) -> None:
    with pytest.raises(RejectedError):
        await filesystem().read({"file_path": path}, CONTEXT)


@pytest.mark.parametrize("level", ["L0", "L1", "L2"])
@pytest.mark.parametrize(
    "path",
    [
        "/custom/tlon/docs/README.md",
        "tlon/stdlib/messages.tlon",
        "//custom/tlon/docs/README.md",
    ],
)
async def test_references_cannot_be_written_even_without_device(level: str, path: str) -> None:
    service = filesystem(level, "/custom")
    with pytest.raises(RejectedError, match="read-only"):
        await service.write({"file_path": path, "content": "corrupt"}, CONTEXT)
    with pytest.raises(RejectedError, match="read-only"):
        await service.edit({"file_path": path, "old_string": "x", "new_string": "y"}, CONTEXT)


async def test_reference_symlink_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "secret").write_text("private")
    (docs / "escape").symlink_to(tmp_path / "secret")
    monkeypatch.setattr(
        "tlon.sandbox.author_files.author_mounts",
        lambda working_dir: {"/workspace/tlon/docs": docs},
    )
    with pytest.raises(RejectedError, match="Symbolic"):
        await filesystem().read({"file_path": "/workspace/tlon/docs/escape"}, CONTEXT)
    with pytest.raises(RejectedError, match="Symbolic"):
        await filesystem().search({"path": "/workspace/tlon/docs", "pattern": "private"}, CONTEXT)


def test_docker_mounts_same_actual_sources_readonly() -> None:
    runtime = object.__new__(DockerRuntime)
    runtime.config = DockerRuntimeConfig(working_dir="/custom")
    runtime.agent_id = "author"
    runtime._paths = MagicMock()
    mounts = runtime._bind_mounts()
    for target, source in author_mounts("/custom").items():
        assert mounts[str(source)] == {"bind": target, "mode": "ro"}


async def test_custom_workspace_does_not_keep_default_reference_alias() -> None:
    with pytest.raises(RejectedError):
        await filesystem(working_dir="/custom").read(
            {"file_path": "/workspace/tlon/docs/README.md"}, CONTEXT
        )


@pytest.mark.parametrize(
    "suffix",
    [
        r"..\secret",
        r"nested\..\..\secret",
        r"C:\secret",
        "README.md:stream",
        "NUL.txt",
        "CON .txt",
        "NUL .txt",
        "COM1 .log",
        "dir. ",
        "bad\x00name",
    ],
)
async def test_windows_author_components_rejected_before_host_join(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "secret").write_text("private", encoding="utf-8")
    monkeypatch.setattr("tlon.sandbox.author_files.sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(
        "tlon.sandbox.author_files.author_mounts",
        lambda working_dir: {"/workspace/tlon/docs": docs},
    )
    for operation, argument in [
        (filesystem().read, "file_path"),
        (filesystem().list_directory, "path"),
    ]:
        with pytest.raises(RejectedError):
            await operation({argument: f"/workspace/tlon/docs/{suffix}"}, CONTEXT)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction semantics")
@pytest.mark.parametrize("mount_root", [False, True])
async def test_windows_author_junctions_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mount_root: bool
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("private", encoding="utf-8")
    docs = tmp_path / "docs"
    if not mount_root:
        docs.mkdir()
    junction = docs if mount_root else docs / "escape"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)], check=True, capture_output=True
    )
    monkeypatch.setattr(
        "tlon.sandbox.author_files.author_mounts",
        lambda working_dir: {"/workspace/tlon/docs": docs},
    )
    logical = "tlon/docs/secret" if mount_root else "tlon/docs/escape/secret"
    try:
        with pytest.raises(RejectedError, match="reparse"):
            await filesystem().read({"file_path": logical}, CONTEXT)
        with pytest.raises(RejectedError, match="reparse"):
            await filesystem().search({"path": "tlon/docs", "pattern": "private"}, CONTEXT)
    finally:
        junction.rmdir()
