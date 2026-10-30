"""Ordinary workspace operations are member-bound and independent of devices."""

import os
from pathlib import Path

import pytest

from tlon.base.config import DockerRuntimeConfig
from tlon.operations.core import CallContext
from tlon.operations.schema import RejectedError, UnknownOutcomeError
from tlon.sandbox.filesystem import MemberFilesystemService

CONTEXT = CallContext("alice", "run", "call")


def service(
    root: Path, level: str = "L0", working_dir: str = "/workspace"
) -> MemberFilesystemService:
    return MemberFilesystemService(
        DockerRuntimeConfig(level=level, working_dir=working_dir),
        None,
        [working_dir],
        [working_dir],
        workspace=root,
    )


@pytest.mark.parametrize("level", ["L0", "L1", "L2"])
@pytest.mark.parametrize("working_dir", ["/workspace", "/custom", "/"])
async def test_all_five_operations_without_docker(
    tmp_path: Path, level: str, working_dir: str
) -> None:
    files = service(tmp_path, level, working_dir)
    created = await files.write(
        {"file_path": "notes/CONTACTS.md", "content": "Alice: friend"}, CONTEXT
    )
    assert not created["existed_before"]
    assert (tmp_path / "notes/CONTACTS.md").read_text() == "Alice: friend"
    assert (
        await files.read({"file_path": f"{working_dir.rstrip('/')}/notes/CONTACTS.md"}, CONTEXT)
    )["content"] == "Alice: friend"
    await files.edit(
        {"file_path": "notes/CONTACTS.md", "old_string": "friend", "new_string": "colleague"},
        CONTEXT,
    )
    assert (await files.search({"pattern": "colleague"}, CONTEXT))["count"] == 1
    assert (await files.list_directory({"path": "notes"}, CONTEXT))["entries"][0][
        "name"
    ] == "CONTACTS.md"
    assert (await files.search({"pattern": "*.md", "mode": "filename"}, CONTEXT))["matches"] == [
        f"{working_dir.rstrip('/')}/notes/CONTACTS.md"
    ]


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/other-agent/workspace/secret",
        "/workspace2/secret",
        "tlon/docs/README.md",
        "tlon/stdlib/agent.tlon",
    ],
)
async def test_cannot_write_outside_workspace_or_author_materials(
    tmp_path: Path, path: str
) -> None:
    with pytest.raises(RejectedError):
        await service(tmp_path).write({"file_path": path, "content": "bad"}, CONTEXT)


@pytest.mark.parametrize("kind", ["file-symlink", "directory-symlink", "hardlink"])
async def test_links_never_expose_or_modify_other_member(tmp_path: Path, kind: str) -> None:
    own = tmp_path / "own"
    own.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    secret = other / "secret"
    secret.write_text("private")
    if kind == "directory-symlink":
        (own / "link").symlink_to(other, target_is_directory=True)
        path = "link/secret"
    elif kind == "file-symlink":
        (own / "link").symlink_to(secret)
        path = "link"
    else:
        os.link(secret, own / "link")
        path = "link"
    files = service(own)
    with pytest.raises(RejectedError):
        await files.read({"file_path": path}, CONTEXT)
    # Link rejection happens before any write, leaving the other member intact.
    with pytest.raises((RejectedError, UnknownOutcomeError)):
        await files.write({"file_path": path, "content": "bad"}, CONTEXT)
    assert secret.read_text() == "private"
    with pytest.raises(RejectedError):
        await files.search({"pattern": "private"}, CONTEXT)


async def test_missing_workspace_binding_does_not_fall_back_to_device() -> None:
    files = MemberFilesystemService(
        DockerRuntimeConfig(level="L1"), None, ["/workspace"], ["/workspace"]
    )
    with pytest.raises(RejectedError, match="not bound"):
        await files.read({"file_path": "CONTACTS.md"}, CONTEXT)


@pytest.mark.skipif(os.name == "nt", reason="POSIX dir_fd replacement hook")
async def test_target_swap_during_write_never_truncates_outside_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target changed after validation is replaced, never followed or truncated."""
    own = tmp_path / "own"
    own.mkdir()
    target = own / "note"
    target.write_text("old")
    outside = tmp_path / "secret"
    outside.write_text("private")
    original_replace = os.replace

    def swap_then_replace(
        source: str, destination: str, *, src_dir_fd: int, dst_dir_fd: int
    ) -> None:
        target.unlink()
        target.symlink_to(outside)
        original_replace(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(os, "replace", swap_then_replace)
    await service(own).write({"file_path": "note", "content": "new"}, CONTEXT)
    assert target.read_text() == "new"
    assert not target.is_symlink()
    assert outside.read_text() == "private"
    assert sorted(p.name for p in own.iterdir()) == ["note"]


@pytest.mark.parametrize("level", ["L0", "L1", "L2"])
async def test_root_workspace_cannot_shadow_readonly_member_home(
    tmp_path: Path, level: str
) -> None:
    files = service(tmp_path, level, "/")
    with pytest.raises(RejectedError, match="read-only"):
        await files.write({"file_path": "/tlon-home/config/RULES.md", "content": "bad"}, CONTEXT)
    with pytest.raises(RejectedError, match="read-only"):
        await files.edit(
            {"file_path": "/tlon-home/config/RULES.md", "old_string": "x", "new_string": "bad"},
            CONTEXT,
        )
    assert not (tmp_path / "tlon-home").exists()
