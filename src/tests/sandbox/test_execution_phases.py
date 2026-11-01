"""Execution diagnostics identify stalled SDK phases without exposing payloads."""

import asyncio
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tlon.base.config import DockerRuntimeConfig
from tlon.base.paths import TlonPaths
from tlon.sandbox import docker_runtime
from tlon.sandbox.docker_runtime import DockerRuntime


@pytest.fixture
def execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[DockerRuntime, MagicMock, MagicMock]:
    monkeypatch.setattr(DockerRuntime, "_create_client", lambda self: MagicMock())
    runtime = DockerRuntime(
        DockerRuntimeConfig(level="L2", network_mode="none"),
        agent_id="phase-test",
        tlon_paths=TlonPaths(home=tmp_path),
    )
    unit = runtime.client.containers.create.return_value
    unit.wait.return_value = {"StatusCode": 0}
    unit.logs.return_value = b"private-output"
    logger = MagicMock()
    monkeypatch.setattr(docker_runtime, "logger", logger)
    return runtime, unit, logger


async def test_all_phases_report_duration_without_command_or_output(
    execution: tuple[DockerRuntime, MagicMock, MagicMock],
) -> None:
    runtime, unit, logger = execution
    result = await runtime.run("private-command")
    records = [
        call.kwargs
        for call in logger.info.call_args_list
        if call.args == ("execution_container_phase_finished",)
    ]
    assert [record["phase"] for record in records] == [
        "create",
        "start",
        "wait",
        "logs_stdout",
        "logs_stderr",
        "cleanup",
    ]
    assert all(record["duration_seconds"] >= 0 for record in records)
    assert all(record["outcome"] == "completed" for record in records)
    assert len({record["container_name"] for record in records}) == 1
    assert result.stdout == "private-output"
    assert "private" not in str(logger.mock_calls)
    unit.remove.assert_called_once_with(force=True)


@pytest.mark.parametrize("phase", ["wait", "logs_stdout", "cleanup"])
async def test_sdk_error_reports_phase_and_type_without_payload(
    execution: tuple[DockerRuntime, MagicMock, MagicMock], phase: str
) -> None:
    runtime, unit, logger = execution
    method = {"wait": unit.wait, "logs_stdout": unit.logs, "cleanup": unit.remove}[phase]
    method.side_effect = RuntimeError("private-token-from-sdk-payload")
    await runtime.run("private-command")
    records = [
        call.kwargs
        for call in logger.info.call_args_list
        if call.args == ("execution_container_phase_finished",)
    ]
    failure = next(record for record in records if record["phase"] == phase)
    assert failure["outcome"] == "failed"
    assert failure["error_type"] == "RuntimeError"
    assert records[-1]["phase"] == "cleanup"
    assert "private" not in str(logger.mock_calls)
    unit.remove.assert_called_once_with(force=True)


async def test_cancellation_identifies_wait_and_preserves_cleanup(
    execution: tuple[DockerRuntime, MagicMock, MagicMock],
) -> None:
    runtime, unit, logger = execution
    entered = threading.Event()
    release = threading.Event()

    def wait() -> dict[str, int]:
        entered.set()
        release.wait(5)
        return {"StatusCode": 0}

    unit.wait.side_effect = wait
    task = asyncio.create_task(runtime.run("private-command"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
    finally:
        release.set()
    records = [
        call.kwargs
        for call in logger.info.call_args_list
        if call.args == ("execution_container_phase_finished",)
    ]
    failure = next(record for record in records if record["phase"] == "wait")
    assert failure["outcome"] == "cancelled"
    assert failure["error_type"] == "CancelledError"
    assert records[-1]["phase"] == "cleanup"
    unit.remove.assert_called_once_with(force=True)
    assert "private" not in str(logger.mock_calls)
