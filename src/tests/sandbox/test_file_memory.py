"""Ordinary private note access preserves member and execution-record boundaries."""

from pathlib import Path

import pytest

from tlon.base.config import DockerRuntimeConfig
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.filesystem import make_filesystem_operations
from tlon.operations.schema import RejectedError, UnknownOutcomeError
from tlon.sandbox.filesystem import MemberFilesystemService


def _executor(workspace: Path, member: str) -> OperationExecutor:
    service = MemberFilesystemService(
        DockerRuntimeConfig(level="L0"), None, [], [], workspace=workspace
    )
    return OperationExecutor(
        OperationRegistry(make_filesystem_operations(service)),
        CallContext(member, "notes-run", "notes-call"),
    )


async def test_notes_are_private_and_cannot_escape_through_paths_or_symlinks(
    tmp_path: Path,
) -> None:
    alice = tmp_path / "alice" / "workspace"
    bob = tmp_path / "bob" / "workspace"
    alice.mkdir(parents=True)
    bob.mkdir(parents=True)
    first = _executor(alice, "alice")
    second = _executor(bob, "bob")
    arguments = {"file_path": "memory/preferences.md", "content": "private-alice-preference"}
    await first.call("file-write", arguments)
    await second.call("file-write", {**arguments, "content": "private-bob-preference"})
    assert (await first.call("file-read", {"file_path": arguments["file_path"]}))[
        "content"
    ] == arguments["content"]
    assert (await second.call("file-read", {"file_path": arguments["file_path"]}))[
        "content"
    ] == "private-bob-preference"
    (alice / "memory" / "outside").symlink_to(bob / "memory", target_is_directory=True)
    database = alice.parent / "program.sqlite3"
    database.write_bytes(b"execution evidence")
    (alice / "memory" / "history").symlink_to(database)
    for path in [
        "memory/outside/preferences.md",
        "memory/history",
        "../program.sqlite3",
        str(bob / "memory" / "preferences.md"),
    ]:
        with pytest.raises(RejectedError):
            await first.call("file-read", {"file_path": path})
        with pytest.raises((RejectedError, UnknownOutcomeError)):
            await first.call("file-write", {"file_path": path, "content": "overwrite"})
    with pytest.raises(RejectedError):
        await first.call("file-search", {"path": "memory", "pattern": "private"})
    (alice / "memory" / "outside").unlink()
    (alice / "memory" / "history").unlink()
    found = await first.call("file-search", {"path": "memory", "pattern": "private"})
    assert found["count"] == 1
    assert "private-bob" not in str(found)
    assert database.read_bytes() == b"execution evidence"
    assert (bob / "memory" / "preferences.md").read_text(
        encoding="utf-8"
    ) == "private-bob-preference"
