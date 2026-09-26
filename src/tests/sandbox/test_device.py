"""Tests for the ExecutionDevice interface and AuditedExecutionDevice.

Covers:
a) DockerRuntime structurally satisfies the runtime_checkable Protocol.
b) AuditedExecutionDevice passes run arguments/results through untouched.
c) Every run emits a ``device_exec`` structlog event and a ``device.exec``
   span with creation-time and end-of-run attributes.
d) The span path is safe without an initialized tracer provider
   (NonRecordingSpan).
e) read_file / write_file / file_exists / close pass through (audit scope
   is exec only).
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from structlog.testing import capture_logs

from tlon.base.config import DockerRuntimeConfig
from tlon.base.paths import TlonPaths
from tlon.sandbox.device import (
    AuditedExecutionDevice,
    ExecutionDevice,
    ExecutionResult,
)
from tlon.sandbox.docker_runtime import DockerRuntime


class _FakeDevice:
    """In-memory ExecutionDevice recording every call."""

    def __init__(self, result: ExecutionResult | None = None) -> None:
        self.result = result or ExecutionResult(exit_code=0, stdout="ok", stderr="")
        self.run_calls: list[tuple[str, int, str | None]] = []
        self.read_calls: list[str] = []
        self.write_calls: list[tuple[str, bytes]] = []
        self.exists_calls: list[str] = []
        self.closed = 0

    async def run(
        self, program: str, timeout: int = 60, workdir: str | None = None
    ) -> ExecutionResult:
        self.run_calls.append((program, timeout, workdir))
        return self.result

    async def read_file(self, path: str) -> bytes:
        self.read_calls.append(path)
        return b"content"

    async def write_file(self, path: str, content: bytes) -> bool:
        self.write_calls.append((path, content))
        return True

    async def file_exists(self, path: str) -> bool:
        self.exists_calls.append(path)
        return True

    def close(self) -> None:
        self.closed += 1


def test_docker_runtime_satisfies_execution_device_protocol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """isinstance check against the runtime_checkable Protocol."""
    monkeypatch.setattr(DockerRuntime, "_create_client", lambda self: MagicMock())
    runtime = DockerRuntime(
        DockerRuntimeConfig(), agent_id="dev-001", tlon_paths=TlonPaths(home=tmp_path)
    )

    assert isinstance(runtime, ExecutionDevice)


def test_audited_device_satisfies_execution_device_protocol() -> None:
    assert isinstance(AuditedExecutionDevice(_FakeDevice(), agent_id="a-1"), ExecutionDevice)


async def test_run_passes_arguments_and_result_through() -> None:
    inner = _FakeDevice(ExecutionResult(exit_code=3, stdout="partial", stderr="boom"))
    device = AuditedExecutionDevice(inner, agent_id="a-1")

    result = await device.run("echo hi", timeout=30, workdir="/workspace")

    assert inner.run_calls == [("echo hi", 30, "/workspace")]
    assert result is inner.result


async def test_run_emits_device_exec_log() -> None:
    inner = _FakeDevice(ExecutionResult(exit_code=0, stdout="ok", stderr=""))
    device = AuditedExecutionDevice(inner, agent_id="a-1")

    with capture_logs() as logs:
        await device.run("echo hi", timeout=30, workdir="/workspace")

    event = next(log for log in logs if log["event"] == "device_exec")
    assert event["log_level"] == "info"
    assert event["agent_id"] == "a-1"
    assert event["workdir"] == "/workspace"
    assert event["command"] == "echo hi"
    assert event["exit_code"] == 0
    assert event["timed_out"] is False
    assert isinstance(event["duration_ms"], int)


async def test_run_marks_timeout_semantics() -> None:
    inner = _FakeDevice(
        ExecutionResult(exit_code=-1, stdout="", stderr="Command timed out after 60 seconds")
    )
    device = AuditedExecutionDevice(inner, agent_id="a-1")

    with capture_logs() as logs:
        await device.run("sleep 999")

    event = next(log for log in logs if log["event"] == "device_exec")
    assert event["timed_out"] is True
    assert event["workdir"] is None


async def test_run_truncates_command_in_audit_records() -> None:
    inner = _FakeDevice()
    device = AuditedExecutionDevice(inner, agent_id="a-1")
    long_command = "x" * 500

    with capture_logs() as logs:
        await device.run(long_command)

    event = next(log for log in logs if log["event"] == "device_exec")
    assert event["command"] == "x" * 200
    # The full command still reaches the inner device untouched.
    assert inner.run_calls[0][0] == long_command


async def test_run_without_tracer_provider_is_safe() -> None:
    """No initialized provider → NonRecordingSpan; set_attribute is a no-op."""
    device = AuditedExecutionDevice(_FakeDevice(), agent_id="a-1")

    result = await device.run("echo hi")

    assert result.exit_code == 0


async def test_run_records_device_exec_span(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider)

    inner = _FakeDevice(ExecutionResult(exit_code=2, stdout="", stderr="bad"))
    device = AuditedExecutionDevice(inner, agent_id="a-1")
    await device.run("false", workdir="/workspace")

    span = next(s for s in exporter.get_finished_spans() if s.name == "device.exec")
    attrs = dict(span.attributes or {})
    assert attrs["tlon.source"] == "sandbox"
    assert attrs["agent_id"] == "a-1"
    assert attrs["workdir"] == "/workspace"
    assert attrs["command"] == "false"
    assert attrs["exit_code"] == 2
    assert attrs["timed_out"] is False
    assert isinstance(attrs["duration_ms"], int)


async def test_file_io_and_close_pass_through() -> None:
    inner = _FakeDevice()
    device = AuditedExecutionDevice(inner, agent_id="a-1")

    assert await device.read_file("/workspace/a.txt") == b"content"
    assert await device.write_file("/workspace/a.txt", b"data") is True
    assert await device.file_exists("/workspace/a.txt") is True
    device.close()
    device.close()

    assert inner.read_calls == ["/workspace/a.txt"]
    assert inner.write_calls == [("/workspace/a.txt", b"data")]
    assert inner.exists_calls == ["/workspace/a.txt"]
    assert inner.closed == 2  # pass-through; idempotence is the inner device's job
    assert inner.run_calls == []  # file I/O is not audited as exec
