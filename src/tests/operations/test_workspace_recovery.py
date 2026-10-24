"""Workspace files share durable read and write receipts with all operations."""

from pathlib import Path

from tlon.agents.program_store import ProgramStore
from tlon.base.config import DockerRuntimeConfig
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.filesystem import make_filesystem_operations
from tlon.sandbox.filesystem import MemberFilesystemService


def executor(workspace: Path, database: Path) -> OperationExecutor:
    service = MemberFilesystemService(
        DockerRuntimeConfig(level="L0"), None, [], [], workspace=workspace
    )
    return OperationExecutor(
        OperationRegistry(make_filesystem_operations(service)),
        CallContext("a", "root", "read"),
        ProgramStore(database, build_id="workspace-test"),
    )


async def test_replayed_read_keeps_snapshot_and_fresh_call_sees_current_file(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    book = workspace / "CONTACTS.md"
    book.write_text("original", encoding="utf-8")
    database = tmp_path / "program.sqlite3"
    first = executor(workspace, database)
    try:
        original = await first.call("file-read", {"file_path": "CONTACTS.md"})
    finally:
        first.store.close()
    book.write_text("updated", encoding="utf-8")
    recovered = executor(workspace, database)
    try:
        assert await recovered.call("file-read", {"file_path": "CONTACTS.md"}) == original
        current = await recovered.call(
            "file-read", {"file_path": "CONTACTS.md"}, CallContext("a", "root", "fresh")
        )
        assert original["content"] == "original" and current["content"] == "updated"
    finally:
        recovered.store.close()


async def test_replayed_write_does_not_overwrite_later_changes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "program.sqlite3"
    arguments = {"file_path": "CONTACTS.md", "content": "first write"}
    first = executor(workspace, database)
    try:
        receipt = await first.call("file-write", arguments)
    finally:
        first.store.close()
    (workspace / "CONTACTS.md").write_text("later change", encoding="utf-8")
    recovered = executor(workspace, database)
    try:
        assert await recovered.call("file-write", arguments) == receipt
        assert (workspace / "CONTACTS.md").read_text(encoding="utf-8") == "later change"
    finally:
        recovered.store.close()
