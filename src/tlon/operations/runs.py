"""Read prior member-owned program results without owning continuation policy."""

import asyncio
from typing import Any, Protocol

from tlon.operations.core import CallContext, Operation
from tlon.operations.run_digest import render_run_digest
from tlon.operations.schema import OperationError, RejectedError


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


class RunReadPort(Protocol):
    async def read_events(self, run_id: str) -> list[dict[str, Any]]: ...


def make_run_read_operations(
    logs: RunReadPort | None = None, member_id: str | None = None
) -> tuple[Operation, ...]:
    """Expose member-owned execution records without any memory service."""

    async def read(context: CallContext, args: dict[str, Any]) -> dict[str, Any]:
        if logs is None or member_id is None:
            raise OperationError("run journal is not bound")
        if context.member_id != member_id:
            raise OperationError("run journal belongs to another member")
        run_id = args["run_id"]
        try:
            events = await logs.read_events(run_id)
        except Exception as error:
            raise OperationError(str(error)) from error
        if not events:
            raise RejectedError(f"run not found: {run_id}", {"outcome": "rejected"})
        return {
            "run_id": run_id,
            "digest": render_run_digest(events),
            "event_count": len(events),
        }

    text = {"type": "string"}
    return (
        Operation(
            "run-read",
            "回读自己的交互来源，原始 run_id，返回有界摘要；只读且不恢复执行。",
            {
                "type": "object",
                "properties": {"run_id": {"type": "string", "minLength": 1}},
                "required": ["run_id"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "run_id": text,
                    "digest": text,
                    "event_count": {"type": "integer"},
                },
                "required": ["run_id", "digest", "event_count"],
                "additionalProperties": False,
            },
            read,
            effects=frozenset({"read"}),
        ),
    )


def run_read_operation_definitions() -> tuple[Operation, ...]:
    return make_run_read_operations()
