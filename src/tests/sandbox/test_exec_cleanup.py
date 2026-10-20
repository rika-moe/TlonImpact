"""Late Docker SDK completions cannot outlive a cancelled execution."""

import asyncio
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.sandbox.test_docker_runtime import _runtime_with_exec_unit


@pytest.mark.parametrize("stage", ["create", "start"])
async def test_cancel_waits_for_late_sdk_completion_and_removes_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    runtime, unit = _runtime_with_exec_unit(monkeypatch, tmp_path, wait_result={"StatusCode": 0})
    entered = threading.Event()
    release = threading.Event()

    def delayed() -> object:
        entered.set()
        assert release.wait(5)
        return unit if stage == "create" else None

    if stage == "create":
        runtime.client.containers.create = MagicMock(side_effect=lambda **kwargs: delayed())
    else:
        unit.start.side_effect = delayed
    call = asyncio.create_task(runtime.run("write-with-side-effect"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        call.cancel()
        await asyncio.sleep(0)
        assert not call.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(call, 3)
    finally:
        release.set()
    unit.remove.assert_called_once_with(force=True)
    unit.wait.assert_not_called()
    if stage == "create":
        unit.start.assert_not_called()


async def test_repeated_cancel_during_remove_still_waits_for_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, unit = _runtime_with_exec_unit(monkeypatch, tmp_path, wait_result={"StatusCode": 0})
    entered = threading.Event()
    release = threading.Event()

    def remove(**kwargs: object) -> None:
        entered.set()
        assert release.wait(5)

    unit.remove.side_effect = remove
    call = asyncio.create_task(runtime.run("echo done"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        call.cancel()
        await asyncio.sleep(0)
        call.cancel()
        await asyncio.sleep(0)
        assert not call.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(call, 3)
    finally:
        release.set()
    unit.remove.assert_called_once_with(force=True)
