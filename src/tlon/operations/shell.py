"""Canonical shell contracts, shared by DSL and Tool projections."""

from typing import Any

from tlon.operations.core import CallContext, Operation
from tlon.operations.schema import UnknownOutcomeError
from tlon.sandbox.shell import DEFAULT_TIMEOUT, MemberShellService

_OUTPUT = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string"},
        "status": {"type": "string", "enum": ["running", "completed"]},
        "exit_code": {"type": ["integer", "null"]},
        "stdout": {"type": "string"},
        "stderr": {"type": "string"},
    },
    "required": ["task_id", "status", "exit_code", "stdout", "stderr"],
    "additionalProperties": False,
}


def make_shell_operations(service: MemberShellService) -> tuple[Operation, ...]:
    async def run(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        try:
            return await service.run(args)
        except UnknownOutcomeError as error:
            error.data["call_id"] = context.call_id
            raise

    async def check(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        return await service.check(args)

    return (
        Operation(
            "shell-run",
            "在一次性容器执行命令，仅 /workspace 文件持久保留；background 默认为 false，timeout 默认为 60 秒。后台执行返回本成员 task_id。",
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "minLength": 1},
                    "background": {"type": "boolean"},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 300},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            _OUTPUT,
            run,
            effects=frozenset({"write"}),
            default_timeout=DEFAULT_TIMEOUT,
            timeout_grace=10,
        ),
        Operation(
            "shell-check",
            "查询本成员后台任务的状态及输出。",
            {
                "type": "object",
                "properties": {"task_id": {"type": "string", "minLength": 1}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
            _OUTPUT,
            check,
            effects=frozenset({"read"}),
        ),
    )


def shell_operation_definitions() -> tuple[Operation, ...]:
    from tlon.base.config import DockerRuntimeConfig

    return make_shell_operations(MemberShellService(DockerRuntimeConfig(level="L0"), None))
