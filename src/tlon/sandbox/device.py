"""ExecutionDevice interface and interface-level exec auditing.

Defines the abstract boundary every agent execution environment sits
behind (design: docs/design/execution-device.md). ``DockerRuntime``
(docker_runtime.py) is the first implementation (L2 of the capability
ladder); capability declaration in member config and further
implementations arrive with real demand.

Deliberate minimalism: the design doc's full form
``run(program, inputs, budget) -> outcome`` (inputs/budget dataclasses,
resource-metered outcome) is postponed until a second implementation
exists. With exactly one implementation the interface stays at the
minimal shape that implementation already exposes — no speculative
abstraction.

``AuditedExecutionDevice`` hangs exec auditing on the interface boundary
(constitution 2: everything the device does must be readable by the
whole society): every ``run`` emits a ``device.exec`` trace span plus a
``device_exec`` structlog event. Only exec is audited — file I/O and
lifecycle pass through untouched.
"""

import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import structlog
from opentelemetry import trace

from tlon.base.logger import get_logger
from tlon.base.tracing import trace_context

#: Commands are truncated in audit records (span attribute + log event);
#: the full text already lives in the session journal's tool-call record.
_COMMAND_AUDIT_LIMIT = 200


@dataclass
class ExecutionResult:
    """Result of a command execution."""

    exit_code: int
    stdout: str
    stderr: str


@runtime_checkable
class ExecutionDevice(Protocol):
    """Abstract agent execution environment.

    Minimal form (see module docstring): signatures match what the first
    implementation (``DockerRuntime``) already provides. ``run`` is the
    only audited operation; file I/O and ``close`` are plumbing.
    """

    async def run(
        self, program: str, timeout: int = 60, workdir: str | None = None
    ) -> ExecutionResult:
        """Execute a program (e.g. a shell command) inside the device."""
        ...

    async def read_file(self, path: str) -> bytes:
        """Read a file's raw bytes from inside the device."""
        ...

    async def write_file(self, path: str, content: bytes) -> bool:
        """Write raw bytes to a file inside the device."""
        ...

    async def file_exists(self, path: str) -> bool:
        """Check whether a path exists inside the device."""
        ...

    def close(self) -> None:
        """Release the device's external resources (client connections).

        Idempotent. The execution environment itself (e.g. the container)
        is unaffected per ADR 0006.
        """
        ...


def _is_timed_out(result: ExecutionResult) -> bool:
    """Current DockerRuntime timeout semantics: exit_code -1 and the
    'Command timed out' stderr marker."""
    return result.exit_code == -1 and result.stderr.startswith("Command timed out")


class AuditedExecutionDevice:
    """ExecutionDevice wrapper that audits every ``run``.

    Each execution is wrapped in a ``device.exec`` trace span (source
    ``sandbox``) carrying agent_id / workdir / truncated command at
    creation and exit_code / timed_out / duration_ms at completion, and
    emits a ``device_exec`` info log with the same fields. All other
    methods pass through to the inner device untouched — the audit scope
    is exec only.
    """

    def __init__(
        self,
        inner: ExecutionDevice,
        agent_id: str,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        """Wrap an execution device with exec auditing.

        Args:
            inner: The device to delegate to.
            agent_id: Owning member's id, attached to every audit record.
            logger: Optional logger override (defaults to this module's).
        """
        self._inner = inner
        self._agent_id = agent_id
        self._logger = logger if logger is not None else get_logger(__name__)

    async def run(
        self, program: str, timeout: int = 60, workdir: str | None = None
    ) -> ExecutionResult:
        """Run through the inner device, recording a span and a log event."""
        with trace_context.start_span(
            "device.exec",
            source="sandbox",
            attributes={
                "agent_id": self._agent_id,
                "workdir": workdir,
                "command": program[:_COMMAND_AUDIT_LIMIT],
            },
        ):
            start = time.perf_counter()
            result = await self._inner.run(program, timeout=timeout, workdir=workdir)
            duration_ms = int((time.perf_counter() - start) * 1000)
            timed_out = _is_timed_out(result)
            # End-of-run values go on the span via set_attribute — the same
            # pattern as llm_client / tool_executor; a no-op on a
            # NonRecordingSpan (no tracer provider initialized).
            span = trace.get_current_span()
            span.set_attribute("exit_code", result.exit_code)
            span.set_attribute("timed_out", timed_out)
            span.set_attribute("duration_ms", duration_ms)
            self._logger.info(
                "device_exec",
                agent_id=self._agent_id,
                workdir=workdir,
                command=program[:_COMMAND_AUDIT_LIMIT],
                exit_code=result.exit_code,
                timed_out=timed_out,
                duration_ms=duration_ms,
            )
            return result

    async def read_file(self, path: str) -> bytes:
        """Pass through to the inner device (audit scope is exec only)."""
        return await self._inner.read_file(path)

    async def write_file(self, path: str, content: bytes) -> bool:
        """Pass through to the inner device (audit scope is exec only)."""
        return await self._inner.write_file(path, content)

    async def file_exists(self, path: str) -> bool:
        """Pass through to the inner device (audit scope is exec only)."""
        return await self._inner.file_exists(path)

    def close(self) -> None:
        """Pass through to the inner device (MemberRuntime's shutdown path)."""
        self._inner.close()
