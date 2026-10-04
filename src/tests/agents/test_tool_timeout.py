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
from tlon.agents.context_pressure import ContextPressure
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.member_spec import MemberSpec, validate_member_spec
from tlon.agents.session_journal import SessionJournal
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.trigger import AgentIdentity
from tlon.base.llm_client import ToolCall
from tlon.base.paths import TlonPaths
from tlon.tools.base import Tool, ToolResult
from tlon.tools.spill import ToolSpill
from tlosure.edn import loads

COGNITIVE_EDN = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :loop {:context {:sections [(persona) (protocol)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :reflect {:enabled false :interval-minutes 5}}}]}
"""


class _SelfLimitedTool(Tool):
    """Declares its own ``timeout`` parameter (like ExecuteBashTool)."""

    def __init__(self, delay: float) -> None:
        super().__init__(
            name="selfLimited",
            description="Sleeps for `delay`; self-limits at the declared timeout.",
            parameters={
                "type": "object",
                "properties": {
                    "timeout": {
                        "type": "integer",
                        "default": 60,
                        "minimum": 1,
                        "maximum": 300,
                    },
                },
            },
        )
        self._delay = delay

    async def execute(self, **kwargs: Any) -> ToolResult:
        await asyncio.sleep(self._delay)
        return ToolResult(success=True, data={"slept": self._delay})


class _PlainTool(Tool):
    """No timeout parameter: the engine per-tool limit stays the authority."""

    def __init__(self, delay: float) -> None:
        super().__init__(
            name="plain",
            description="Sleeps for `delay`.",
            parameters={"type": "object", "properties": {}},
        )
        self._delay = delay

    async def execute(self, **kwargs: Any) -> ToolResult:
        await asyncio.sleep(self._delay)
        return ToolResult(success=True, data={"slept": self._delay})


def _make_driver(
    tmp_path: Path, tool_executor: ToolExecutor, per_tool_timeout: float = 0.1
) -> LoopDriver:
    spec = MemberSpec.from_edn(loads(COGNITIVE_EDN))
    validate_member_spec(spec)
    journal = SessionJournal("agent1", TlonPaths(home=tmp_path))
    return LoopDriver(
        identity=AgentIdentity(id="agent1", name="Agent", address="agent1@tlon.internal"),
        rule=spec.rules[0],
        services=MagicMock(),
        executor=MagicMock(),
        tool_executor=tool_executor,
        per_tool_timeout=per_tool_timeout,
        on_complete=MagicMock(),
        journal=journal,
        spill=ToolSpill(TlonPaths(home=tmp_path), "agent1"),
        pressure=ContextPressure(MagicMock(), journal, 262144),
    )


def _tc(name: str, arguments: dict | None = None) -> ToolCall:
    return ToolCall(id="c1", function_name=name, arguments=arguments or {})


async def test_declared_timeout_runs_past_engine_fallback(tmp_path: Path) -> None:
    """A schema-legal timeout=120 must be honored end-to-end: the tool
    finishes even though the engine per-tool fallback is far shorter."""
    executor = ToolExecutor()
    executor.register_tool(_SelfLimitedTool(delay=0.4))
    driver = _make_driver(tmp_path, executor, per_tool_timeout=0.1)

    _, result = await driver.execute_one_tool(
        "s1", _tc("selfLimited", {"timeout": 120})
    )

    assert result.success
    assert result.data == {"slept": 0.4}


async def test_engine_grace_timeout_names_tool_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the tool itself hangs past its declared timeout, the error names
    the tool's declared limit and the engine grace — not per_tool_timeout."""
    monkeypatch.setattr(loop_driver_module, "_TOOL_TIMEOUT_GRACE", 0.05)
    executor = ToolExecutor()
    executor.register_tool(_SelfLimitedTool(delay=5.0))
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
    executor.register_tool(_PlainTool(delay=5.0))
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
