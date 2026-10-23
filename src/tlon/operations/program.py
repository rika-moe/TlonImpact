"""Read-only validation of complete author source packages."""

from collections.abc import Callable
from typing import Any

from tlon.operations.core import CallContext, Operation
from tlon.operations.schema import OperationError

ProgramChecker = Callable[[str, dict[str, Any]], dict[str, Any]]


def program_check_operation(checker: ProgramChecker | None = None) -> Operation:
    """Bind admission at assembly; inspecting this contract reads no sources."""

    async def check(_context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        if checker is None:
            raise OperationError("program checker is not bound")
        return checker(args["behavior"], args["modules"])

    return Operation(
        "program-check",
        "静态校验完整 v2 Behavior 与 modules，不执行程序、不发布、不批准。错误以 diagnostics 返回；读取 tlon/docs/README.md 获取作者参考。",
        {
            "type": "object",
            "properties": {
                "behavior": {"type": "string"},
                "modules": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": True,
                },
            },
            "required": ["behavior", "modules"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "valid": {"type": "boolean"},
                "digest": {"type": "string"},
                "diagnostics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["valid", "digest", "diagnostics"],
            "additionalProperties": False,
        },
        check,
        effects=frozenset({"read"}),
    )
