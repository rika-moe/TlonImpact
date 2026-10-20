"""Tool timeout authority (docs/reference/tools-reference.md): the schema
the model sees must be honored.

A tool declaring a ``timeout`` parameter self-limits at that value, so the
engine's ``wait_for`` waits the declared value plus a small grace — the
engine per-tool limit (``tool.per_timeout``) is only the fallback for
tools that declare no timeout of their own. Timeout errors name the real
limit and the layer it comes from.
"""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import tlon.agents.loop_driver as loop_driver_module
from tests.recipe_helpers import recipe_for
from tlon.agents.context_pressure import ContextPressure
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.member_spec import MemberSpec, validate_member_spec
from tlon.agents.session_journal import SessionJournal
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.trigger import AgentIdentity
from tlon.base.llm_client import ToolCall
from tlon.base.paths import TlonPaths
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.tools.spill import ToolSpill
from tlosure.edn import loads

SESSION_EDN = '{:version 1 :rules [{:name "respond" :on :mail-delivered :actions [(run-loop (quote {:context {:sections [(persona) (protocol)]} :stages [(think) (act)] :until (or (iteration-limit-reached?) (error-limit-reached?)) :max-iterations 10 :max-consecutive-errors 3 :max-tokens 1000000 :reflect {:enabled false :interval-minutes 5}}))]}]}'


def _sleeping_tool(delay: float, *, declared: bool) -> OperationTool:
    async def handler(context: CallContext, arguments: dict[str, Any]) -> Any:
        await asyncio.sleep(delay)
        return {"slept": delay}

    name = "selfLimited" if declared else "plain"
    properties = {"timeout": {"type": "integer", "minimum": 1, "maximum": 300}} if declared else {}
    operation = Operation(
        name,
        "Sleeps",
        {"type": "object", "properties": properties, "required": [], "additionalProperties": False},
        True,
        handler,
        effects=frozenset({"read"}),
        default_timeout=60 if declared else None,
    )
    execution = OperationExecutor(OperationRegistry([operation]), CallContext("agent1", "r", "c"))
    return OperationTool(execution, name)


def _make_driver(
    tmp_path: Path, tool_executor: ToolExecutor, per_tool_timeout: float = 0.1
) -> LoopDriver:
    spec = MemberSpec.from_edn(loads(SESSION_EDN))
    validate_member_spec(spec)
    journal = SessionJournal("agent1", TlonPaths(home=tmp_path))
    return LoopDriver(
        identity=AgentIdentity(id="agent1", name="Agent", address="agent1@tlon.internal"),
        recipe=recipe_for(spec.rules[0]),
        services=MagicMock(),
        executor=MagicMock(),
        tool_executor=tool_executor,
        per_tool_timeout=per_tool_timeout,
        journal=journal,
        spill=ToolSpill(TlonPaths(home=tmp_path), "agent1"),
        pressure=ContextPressure(MagicMock(), journal, 262144),
        is_human_sender=lambda address: True,
    )


def _tc(name: str, arguments: dict | None = None) -> ToolCall:
    return ToolCall(id="c1", function_name=name, arguments=arguments or {})


async def test_declared_timeout_runs_past_engine_fallback(tmp_path: Path) -> None:
    """A schema-legal timeout=120 must be honored end-to-end: the tool
    finishes even though the engine per-tool fallback is far shorter."""
    executor = ToolExecutor()
    executor.register_tool(_sleeping_tool(0.4, declared=True))
    driver = _make_driver(tmp_path, executor, per_tool_timeout=0.1)

    _, result = await driver.execute_one_tool("s1", _tc("selfLimited", {"timeout": 120}))

    assert result.success
    assert result.data == {"slept": 0.4}


async def test_engine_grace_timeout_names_tool_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the tool itself hangs past its declared timeout, the error names
    the tool's declared limit and the engine grace — not per_tool_timeout."""
    monkeypatch.setattr(loop_driver_module, "_TOOL_TIMEOUT_GRACE", 0.05)
    executor = ToolExecutor()
    executor.register_tool(_sleeping_tool(5.0, declared=True))
    driver = _make_driver(tmp_path, executor, per_tool_timeout=30.0)

    _, result = await driver.execute_one_tool("s1", _tc("selfLimited", {"timeout": 1}))

    assert not result.success
    assert "Timeout after 1.05s" in result.error
    assert "declared timeout 1s" in result.error
    assert "grace" in result.error


async def test_tool_without_timeout_keeps_engine_fallback(tmp_path: Path) -> None:
    """Tools without a timeout parameter: per_tool_timeout semantics are
    unchanged, and the error names the engine layer."""
    executor = ToolExecutor()
    executor.register_tool(_sleeping_tool(5.0, declared=False))
    driver = _make_driver(tmp_path, executor, per_tool_timeout=0.1)

    _, result = await driver.execute_one_tool("s1", _tc("plain"))

    assert not result.success
    assert "Timeout after 0.1s" in result.error
    assert "tool.per_timeout" in result.error
    assert "declares no timeout" in result.error


async def test_unknown_tool_falls_back_to_engine_limit(tmp_path: Path) -> None:
    """A missing tool cannot declare a timeout: engine fallback applies and
    the executor's own 'not found' error comes back within the budget."""
    executor = ToolExecutor()
    driver = _make_driver(tmp_path, executor, per_tool_timeout=0.1)

    _, result = await driver.execute_one_tool("s1", _tc("nope"))

    assert not result.success
    assert "Tool not found" in result.error


@pytest.mark.parametrize(
    "arguments, expected", [({"command": "work"}, 60), ({"command": "work", "timeout": 120}, 120)]
)
async def test_operation_shell_timeout_uses_shared_default_and_explicit_value(
    tmp_path: Path, arguments: dict[str, Any], expected: int
) -> None:
    from unittest.mock import AsyncMock

    from tlon.base.config import DockerRuntimeConfig
    from tlon.operations.adapters import OperationTool
    from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
    from tlon.operations.shell import make_shell_operations
    from tlon.sandbox.device import ExecutionDevice, ExecutionResult
    from tlon.sandbox.shell import MemberShellService

    async def run(*args: Any, **kwargs: Any) -> ExecutionResult:
        await asyncio.sleep(0.1)
        return ExecutionResult(0, "done", "")

    device = MagicMock(spec=ExecutionDevice)
    device.run = AsyncMock(side_effect=run)
    execution = OperationExecutor(
        OperationRegistry(
            make_shell_operations(MemberShellService(DockerRuntimeConfig(level="L2"), device))
        ),
        CallContext("agent1", "run", "call"),
    )
    executor = ToolExecutor()
    executor.register_tool(OperationTool(execution, "shell-run"))
    driver = _make_driver(tmp_path, executor, per_tool_timeout=0.01)
    call = _tc("shell-run", arguments)
    seconds, authority = driver._tool_timeout_budget(call)
    assert seconds == expected + loop_driver_module._TOOL_TIMEOUT_GRACE
    assert f"declared timeout {expected}s" in authority
    _, result = await driver.execute_one_tool("s1", call)
    assert result.success, result.error
    assert result.data["stdout"] == "done"
    device.run.assert_awaited_once_with("work", timeout=expected)
