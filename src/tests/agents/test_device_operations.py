"""Runtime exposes one device registry and owns its shutdown order."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from tests.operations.test_filesystem_operations import MemoryDevice
from tlon.agents.member_runtime import MemberCapabilities, MemberRuntime
from tlon.agents.member_spec import MemberSpec
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.config import AgentConfig, DockerRuntimeConfig, LLMConfig
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.operations.core import CallContext
from tlon.operations.filesystem import make_filesystem_operations
from tlon.operations.shell import make_shell_operations
from tlon.operations.time import time_operation_definitions
from tlon.sandbox.device import ExecutionDevice, ExecutionResult
from tlon.sandbox.filesystem import MemberFilesystemService
from tlon.sandbox.shell import MemberShellService
from tlosure.edn import loads


def member_at(
    home: Path, device: ExecutionDevice, actions: str, shell: MemberShellService | None = None
) -> MemberRuntime:
    config = DockerRuntimeConfig(level="L2" if shell else "L1")
    files = MemberFilesystemService(config, device, ["/workspace"], ["/workspace"])
    operations = (*make_filesystem_operations(files), *time_operation_definitions())
    if shell:
        operations = (*operations, *make_shell_operations(shell))
    caps = MemberCapabilities(
        config=AgentConfig(
            id="device-member", name="Device", llm=LLMConfig(model_name="test/model"), docker=config
        ),
        llm_client=MagicMock(),
        memory=MagicMock(),
        task_manager=MagicMock(),
        tool_executor=ToolExecutor(),
        tlon_paths=TlonPaths(home=home),
        docker_runtime=device,
        operations=operations,
        shell_service=shell,
    )
    return MemberRuntime(
        member_id="device-member",
        name="Device",
        event_bus=EventBus(),
        spec=MemberSpec.from_edn(
            loads('{:version 1 :rules [{:name "work" :on :boot :actions [' + actions + "]}]}")
        ),
        builtins=make_member_builtins(),
        capabilities=caps,
    )


async def test_member_program_and_model_share_file_contract(tmp_path: Path) -> None:
    device = MemoryDevice()
    member = member_at(
        tmp_path,
        device,
        '(file-write {:file_path "shared.txt" :content "共享内容"}) (state-put {:key "seen" :value (get (file-read {:file_path "shared.txt"}) :content)})',
    )
    try:
        await member._run_rule_body(member.spec.rules[0], None, run_id="device-run")
        assert member._program_store.state_get("seen") == "共享内容"
        executor = member._capabilities.tool_executor
        result = await executor.execute(
            "file-read",
            {"file_path": "shared.txt"},
            CallContext("device-member", "manual", "read", origin="tool"),
        )
        assert result.success and result.data["content"] == "共享内容"
        assert (
            await executor.execute(
                "time-now", {}, CallContext("device-member", "manual", "time", origin="tool")
            )
        ).success
        assert not (
            await executor.execute(
                "file-write", {"file_path": "other", "content": "bad", "member_id": "victim"}
            )
        ).success
        assert device.files == {"/workspace/shared.txt": "共享内容".encode()}
    finally:
        await member.shutdown()


async def test_shutdown_drains_shell_before_close_and_wake_has_new_handles(tmp_path: Path) -> None:
    order: list[str] = []
    entered = asyncio.Event()
    device = MagicMock(spec=ExecutionDevice)

    async def run(*args: Any, **kwargs: Any) -> ExecutionResult:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            order.append("task stopped")
        return ExecutionResult(0, "", "")

    device.run = AsyncMock(side_effect=run)
    device.close.side_effect = lambda: order.append("device closed")
    shell = MemberShellService(DockerRuntimeConfig(level="L2"), device)
    member = member_at(tmp_path, device, "", shell)
    tool = member._capabilities.tool_executor
    started = await tool.execute("shell-run", {"command": "wait", "background": True})
    assert started.success
    await asyncio.wait_for(entered.wait(), 1)
    await member.shutdown()
    assert order == ["task stopped", "device closed"]
    assert not shell._tasks
    new_device = MagicMock(spec=ExecutionDevice)
    new_shell = MemberShellService(DockerRuntimeConfig(level="L2"), new_device)
    awakened = member_at(tmp_path, new_device, "", new_shell)
    try:
        result = await awakened._capabilities.tool_executor.execute(
            "shell-check",
            {"task_id": started.data["task_id"]},
            CallContext("device-member", "after-wake", "check", origin="tool"),
        )
        assert not result.success
        new_device.run.assert_not_called()
    finally:
        await awakened.shutdown()
