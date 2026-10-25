"""Fault injection checks the persisted truth and the live view together."""

import os
from pathlib import Path
from typing import Any

import pytest

from tlon.base.paths import TlonPaths
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
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
