"""Fault injection checks the persisted truth and the live view together."""

import os
from pathlib import Path
from typing import Any

import pytest

from tlon.base.paths import TlonPaths
from tlon.memory.claim_store import ClaimStore
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.memory import make_memory_operations
from tlon.operations.schema import UnknownOutcomeError
from tlon.operations.tasks import make_task_operations
from tlon.tasks.manager import TaskManager


@pytest.mark.parametrize("failure", ["write", "replace", "after_commit"])
async def test_task_failed_create_no_ghost_or_counter_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    manager = TaskManager("a", TlonPaths(home=tmp_path))
    manager.create_task("committed")
    executor = OperationExecutor(
        OperationRegistry(make_task_operations(manager)), CallContext("a", "run", "call")
    )
    original_replace = os.replace
    original_write = manager._write_lines

    def replace(src: Any, dst: Any) -> None:
        if failure == "after_commit":
            original_replace(src, dst)
        raise OSError("lost receipt" if failure == "after_commit" else "replace failed")

    def write(lines: list[str]) -> None:
        raise OSError("write failed")

    with monkeypatch.context() as patch:
        patch.setattr(manager, "_write_lines", write) if failure == "write" else patch.setattr(
            os, "replace", replace
        )
        with pytest.raises(UnknownOutcomeError):
            await executor.call("task-create", {"title": "uncertain"})
    listing = await executor.call("task-list", {})
    expected = ["committed", "uncertain"] if failure == "after_commit" else ["committed"]
    assert [task["title"] for task in listing["tasks"]] == expected
    assert manager._counter == len(expected)
    next_task = manager.create_task("next")
    assert next_task.id == f"t-{len(expected) + 1:03d}"
    restored = TaskManager("a", TlonPaths(home=tmp_path))
    restored.load()
    assert [task.title for task in restored.list_tasks()] == expected + ["next"]
    assert manager._write_lines == original_write


@pytest.mark.parametrize("action", ["start", "pause", "complete", "remove"])
def test_task_failed_transition_keeps_committed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    manager = TaskManager("a", TlonPaths(home=tmp_path))
    manager.create_task("A")
    if action in ("pause", "complete", "remove"):
        manager.start_task("t-001")
    if action == "remove":
        manager.complete_task("t-001")
    before = manager.get_task("t-001").to_dict()

    def broken(lines: list[str]) -> None:
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(manager, "_write_lines", broken)
        with pytest.raises(OSError):
            getattr(manager, f"{action}_task")("t-001")
    assert manager.get_task("t-001").to_dict() == before
    manager.create_task("next")
    restored = TaskManager("a", TlonPaths(home=tmp_path))
    restored.load()
    assert restored.get_task("t-001").to_dict() == before


@pytest.mark.parametrize("action", ["add", "delete", "supersede"])
@pytest.mark.parametrize("failure", ["write", "replace", "after_commit"])
async def test_claim_failed_mutation_matches_actual_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str, failure: str
) -> None:
    store = ClaimStore("a", tmp_path / "memory")
    await store.add_claim({"id": "old", "text": "committed"})
    original_replace = os.replace
    import tlon.memory.claim_store as module

    original_open = module.aiofiles.open

    def replace(src: Any, dst: Any) -> None:
        if failure == "after_commit":
            original_replace(src, dst)
        raise OSError("replace uncertain")

    def open_file(*args: Any, **kwargs: Any) -> Any:
        if len(args) > 1 and args[1] == "w":
            raise OSError("write failed")
        return original_open(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(module.aiofiles, "open", open_file) if failure == "write" else patch.setattr(
            os, "replace", replace
        )
        with pytest.raises(OSError):
            if action == "add":
                await store.add_claim({"id": "new", "text": "candidate"})
            elif action == "delete":
                await store.delete_claim("old")
            else:
                await store.supersede_claim("old", "new", "today")
    assert module.aiofiles.open == original_open
    restored = ClaimStore("a", tmp_path / "memory")
    committed = await restored.get_all_claims(include_superseded=True)
    assert await store.get_all_claims(include_superseded=True) == committed
    if failure != "after_commit":
        assert committed == [{"id": "old", "text": "committed"}]
    elif action == "add":
        assert [claim["id"] for claim in committed] == ["old", "new"]
    elif action == "delete":
        assert committed == []
    else:
        assert committed[0]["status"] == "superseded"
    await store.add_claim({"id": "next", "text": "next"})
    assert await store.get_all_claims(include_superseded=True) == committed + [
        {"id": "next", "text": "next"}
    ]


async def test_real_memory_write_failure_projects_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    memory = MemoryManager("a", MemoryConfig(), tmp_path / "a")
    # session-read is not used; no journal methods are invoked by memory writes.
    executor = OperationExecutor(
        OperationRegistry(make_memory_operations(memory, None)), CallContext("a", "run", "call")
    )  # type: ignore[arg-type]

    def broken(src: Any, dst: Any) -> None:
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", broken)
        with pytest.raises(UnknownOutcomeError):
            await executor.call("memory-remember", {"fact": "failed"})
    assert await memory.claims.get_all_claims() == []
    await executor.call("memory-remember", {"fact": "committed"})
    assert [claim["text"] for claim in await memory.claims.get_all_claims()] == ["committed"]
