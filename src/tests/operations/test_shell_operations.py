"""Both projections share device results; durable intent prevents unsafe replay."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tlon.agents.member_spec import MemberSpec
from tlon.agents.program_driver import ProgramDriver
from tlon.agents.program_store import ProgramStore
from tlon.base.config import DockerRuntimeConfig
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.schema import InputError, OperationError, UnknownOutcomeError
from tlon.operations.shell import make_shell_operations
from tlon.operations.time import time_operation_definitions
from tlon.sandbox.device import ExecutionResult
from tlon.sandbox.shell import MemberShellService, ShellExecutionError
from tlosure.edn import Keyword, loads
from tlosure.eval import Evaluator


def bindings(device, store: ProgramStore | None = None):
    service = MemberShellService(DockerRuntimeConfig(level="L2"), device)
    executor = OperationExecutor(
        OperationRegistry((*make_shell_operations(service), *time_operation_definitions())),
        CallContext("a", "r", "c"),
        store,
    )
    return service, executor, DSLAdapter(executor)


def program():
    return MemberSpec.from_edn(
        loads(
            '{:version 1 :rules [{:name "shell" :on :boot :actions [(shell-run {:command "touch once"})]}]}'
        )
    ).rules[0]


@pytest.mark.parametrize("disk", [False, True])
@pytest.mark.parametrize("after_submit", [False, True])
async def test_unknown_intent_never_reexecutes(tmp_path: Path, disk, after_submit):
    device = AsyncMock()
    device.run.return_value = ExecutionResult(0, "ok", "")
    store = ProgramStore(tmp_path / "program.db" if disk else None, build_id="test")
    _, executor, adapter = bindings(device, store)
    if after_submit:

        def interrupted(*args):
            raise asyncio.CancelledError()

        store.commit_invocation = interrupted
    else:
        device.run.side_effect = asyncio.CancelledError()

    def driver():
        return ProgramDriver(
            store, "run", adapter.builtins(), AsyncMock(), operations=adapter, member_id="a"
        )

    with pytest.raises(asyncio.CancelledError):
        await driver().run(program(), {})
    calls = device.run.await_count
    if disk:
        store = ProgramStore(tmp_path / "program.db", build_id="test")
        _, executor, adapter = bindings(device, store)
    with pytest.raises(UnknownOutcomeError) as error:
        await driver().run(program(), {})
    assert error.value.data["retry_safe"] is False
    assert device.run.await_count == calls == 1


async def test_recorded_result_replays_without_device():
    device = AsyncMock()
    device.run.return_value = ExecutionResult(0, "ok", "")
    store = ProgramStore(None, build_id="test")
    _, _, adapter = bindings(device, store)
    for _ in range(2):
        await ProgramDriver(
            store, "r", adapter.builtins(), AsyncMock(), operations=adapter, member_id="a"
        ).run(program(), {})
    assert device.run.await_count == 1


async def test_invalid_input_does_not_claim_intent():
    device = AsyncMock()
    store = ProgramStore(None, build_id="test")
    _, _, adapter = bindings(device, store)
    bad = MemberSpec.from_edn(
        loads(
            '{:version 1 :rules [{:name "bad" :on :boot :actions [(shell-run {:command "x" :timeout 0})]}]}'
        )
    ).rules[0]
    with pytest.raises(InputError):
        await ProgramDriver(
            store, "r", adapter.builtins(), AsyncMock(), operations=adapter, member_id="a"
        ).run(bad, {})
    assert not store._invocations
    device.run.assert_not_called()


async def test_dsl_and_tool_nonzero_preserve_same_failure_data():
    device = AsyncMock()
    device.run.return_value = ExecutionResult(7, "partial", "failed")
    _, executor, adapter = bindings(device)
    tool = await OperationTool(executor, "shell-run").execute_context({"command": "false"})
    with pytest.raises(ShellExecutionError) as error:
        await Evaluator(adapter.builtins(), 10000, 100).eval_async(
            loads('(shell-run {:command "false"})'), {}, 0, adapter.handle
        )
    assert not tool.success
    assert tool.data == error.value.data
    assert tool.data["exit_code"] == 7


async def test_dsl_clock_executes_actual_host_read():
    _, _, adapter = bindings(AsyncMock())
    result = await Evaluator(adapter.builtins(), 10000, 100).eval_async(
        loads("(time-now {})"), {}, 0, adapter.handle
    )
    assert result[Keyword("unix")] > 0


@pytest.mark.parametrize(
    "failure", [ExecutionResult(-1, "partial", "Command timed out"), OSError("lost")]
)
async def test_unknown_device_outcome_has_call_identity(failure):
    device = AsyncMock()
    if isinstance(failure, Exception):
        device.run.side_effect = failure
    else:
        device.run.return_value = failure
    _, executor, adapter = bindings(device)
    context = CallContext("a", "run", "stable-call")
    with pytest.raises(UnknownOutcomeError) as failure:
        await OperationTool(executor, "shell-run").execute_context({"command": "effect"}, context)
    assert failure.value.data["call_id"] == "stable-call"
    assert failure.value.data["outcome"] == "unknown"
    assert failure.value.data["retry_safe"] is False


async def test_operation_context_cannot_cross_members():
    device = AsyncMock()
    _, executor, _ = bindings(device)
    with pytest.raises(OperationError, match="another member"):
        await OperationTool(executor, "shell-run").execute_context(
            {"command": "effect"}, CallContext("other", "run", "c")
        )
    device.run.assert_not_called()


@pytest.mark.parametrize("failure", [ExecutionResult(-1, "partial", "timeout"), OSError("lost")])
async def test_background_unknown_preserves_original_task_handle(failure):
    device = AsyncMock()
    if isinstance(failure, Exception):
        device.run.side_effect = failure
    else:
        device.run.return_value = failure
    service, executor, _ = bindings(device)
    started = await OperationTool(executor, "shell-run").execute_context(
        {"command": "effect", "background": True}
    )
    task_id = started.data["task_id"]
    await asyncio.gather(service._tasks[task_id], return_exceptions=True)
    with pytest.raises(UnknownOutcomeError) as failure:
        await OperationTool(executor, "shell-check").execute_context({"task_id": task_id})
    assert failure.value.data["outcome"] == "unknown"
    assert failure.value.data["retry_safe"] is False
    assert failure.value.data["task_id"] == task_id
    await service.shutdown()
