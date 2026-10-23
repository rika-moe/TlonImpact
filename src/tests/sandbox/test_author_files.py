"""The published author materials need no Docker or extra filesystem authority."""

from pathlib import Path
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
