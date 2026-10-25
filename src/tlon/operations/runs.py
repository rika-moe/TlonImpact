"""Read prior member-owned program results without owning continuation policy."""

import asyncio
from typing import Any, Protocol

from tlon.operations.core import CallContext, Operation
from tlon.operations.schema import OperationError


class RunResultReader(Protocol):
    def previous_entry(self, run_id: str) -> dict[str, Any]: ...


def make_run_operations(
    store: RunResultReader | None = None, member_id: str | None = None
) -> tuple[Operation, ...]:
    async def previous(context: CallContext, arguments: dict[str, Any]) -> dict[str, Any]:
        if store is None or member_id is None:
            raise OperationError("program result reader is not bound")
        if context.member_id != member_id:
            raise OperationError("program result reader belongs to another member")
        try:
            return await asyncio.to_thread(store.previous_entry, context.run_id)
        except ValueError as error:
            raise OperationError(str(error)) from error

    text = {"type": "string"}
    return (
        Operation(
            "run-previous",
            "读取自身同入口、当前运行开始前最近成功运行的原始结果与模型契约指纹；程序自行决定是否延续。",
            {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            {
                "type": "object",
                "properties": {
                    "run_id": text,
                    "contract": text,
                    "previous": True,
                },
                "required": ["run_id", "contract", "previous"],
                "additionalProperties": False,
            },
            previous,
            effects=frozenset({"read"}),
        ),
    )


def run_operation_definitions() -> tuple[Operation, ...]:
    return make_run_operations()
