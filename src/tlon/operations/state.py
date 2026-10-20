"""Member-private structured state, shared by program and model entry points."""

import asyncio
from typing import Any, Protocol

from tlon.operations.core import CallContext, Operation
from tlon.operations.invocation import Invocation
from tlon.operations.schema import (
    OperationError,
    RecoveryMismatchError,
    UnknownOutcomeError,
    edn_value,
    json_value,
)


class StateStore(Protocol):
    def state_get(self, key: str) -> Any: ...
    def atomic_state_put(self, invocation: Invocation, key: str, value: Any) -> Any: ...


def make_state_operations(store: StateStore) -> tuple[Operation, ...]:
    async def get(context: CallContext, arguments: dict[str, Any]) -> Any:
        return json_value(await asyncio.to_thread(store.state_get, arguments["key"]), edn_keys=True)

    async def put(context: CallContext, arguments: dict[str, Any]) -> Any:
        value = edn_value(arguments["value"])
        if context.invocation is None:
            raise OperationError("state write requires a durable host invocation")
        try:
            result = await asyncio.to_thread(
                store.atomic_state_put,
                context.invocation,
                arguments["key"],
                value,
            )
        except RecoveryMismatchError:
            raise
        except Exception as error:
            raise UnknownOutcomeError(
                str(error),
                {"call_id": context.call_id, "outcome": "unknown", "retry_safe": True},
            ) from error
        return json_value(result, edn_keys=True)

    key = {"type": "string", "minLength": 1}
    return (
        Operation(
            "state-get",
            "读取自身的结构化状态；不存在返回 null。",
            {
                "type": "object",
                "properties": {"key": key},
                "required": ["key"],
                "additionalProperties": False,
            },
            True,
            get,
            effects=frozenset({"read"}),
        ),
        Operation(
            "state-put",
            "写入自身的严格 JSON 状态并返回该值。",
            {
                "type": "object",
                "properties": {"key": key, "value": True},
                "required": ["key", "value"],
                "additionalProperties": False,
            },
            True,
            put,
            effects=frozenset({"write"}),
            retry_safe=True,
        ),
    )


class _UnboundState:
    def state_get(self, key: str) -> Any:
        raise RuntimeError("state operation requires member services")

    def atomic_state_put(self, invocation: Invocation, key: str, value: Any) -> Any:
        raise RuntimeError("state operation requires member services")


def state_operation_definitions() -> tuple[Operation, ...]:
    return make_state_operations(_UnboundState())
