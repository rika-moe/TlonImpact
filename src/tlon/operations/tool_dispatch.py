"""Program composition port: durable leaf calls, without a batch effect intent."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from tlon.base.execution import current_execution
from tlon.operations.core import CallContext, OperationExecutor
from tlon.operations.invocation import Invocation
from tlon.operations.schema import InputError, RecoveryMismatchError, RejectedError, validate

TOOL_EXECUTE = "tool-execute"
TOOL_EXECUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tools": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
        "tool_calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "name": {"type": "string", "minLength": 1},
                    "arguments": {"type": "object"},
                },
                "required": ["id", "name", "arguments"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["tools", "tool_calls"],
    "additionalProperties": False,
}


class ToolDispatch:
    """Dispatch only frozen, requested member tools through their existing executor."""

    def __init__(
        self,
        executor: OperationExecutor,
        tool_definitions: Callable[[], list[dict[str, Any]]],
    ) -> None:
        self.executor = executor
        self.tool_definitions = tool_definitions

    async def execute(
        self, arguments: dict[str, Any], context: CallContext
    ) -> list[dict[str, Any]]:
        inputs = validate(TOOL_EXECUTE_SCHEMA, arguments)
        scope = current_execution()
        if scope is None:
            raise RecoveryMismatchError("tool-execute requires a program execution scope")
        host = await asyncio.to_thread(scope.store.load_model_context, scope.root_id, "host")
        if host is None:
            raise RecoveryMismatchError("tool-execute requires a frozen member tool catalog")
        frozen = {item["function"]["name"]: item for item in host["tools"]}
        names = set(inputs["tools"])
        if names - frozen.keys():
            raise InputError("tool-execute requested tools outside the frozen member catalog")
        calls = inputs["tool_calls"]
        ids = [item["id"] for item in calls]
        if len(set(ids)) != len(ids) or any(not item.strip() for item in ids):
            raise InputError("tool-execute tool call IDs must be nonempty and unique")
        definitions = {item.name: item for item in self.executor.registry.definitions()}
        for item in calls:
            name = item["name"]
            if name not in names or name not in definitions:
                raise InputError(f"tool-execute tool is outside the requested member scope: {name}")
        results: list[dict[str, Any]] = []
        # Batch metadata participates in every leaf identity, but has no effect intent.
        # A partial replay cannot silently change later calls or provider correlation IDs.
        for index, item in enumerate(calls):
            name = item["name"]
            child = replace(
                context,
                call_id=f"{context.call_id}:tool:{index}",
                call_path=(*context.call_path, "tool", str(index)),
                origin="tool",
                arguments_json=json.dumps(
                    {"batch": inputs, "index": index},
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
            )
            # Check identity before schema validation can turn changed replay input
            # into ordinary model feedback and hide a previous unknown outcome.
            if self.executor.store is not None:
                assert child.arguments_json is not None
                await asyncio.to_thread(
                    self.executor.store.lookup_invocation,
                    Invocation(
                        call_id=child.call_id,
                        root_id=child.run_id,
                        path=child.call_path,
                        name=name,
                        arguments_json=child.arguments_json,
                        definition_id=self.executor.definition_id(definitions[name]),
                        build_id=self.executor.store.build_id,
                    ),
                )
            try:
                found, value = await self.executor.replay(name, item["arguments"], child)
                if not found:
                    current = {
                        entry["function"]["name"]: entry for entry in self.tool_definitions()
                    }
                    if name not in current:
                        raise RecoveryMismatchError(
                            f"tool-execute tool is no longer authorized: {name}"
                        )
                    if current[name] != frozen[name]:
                        raise RecoveryMismatchError(
                            "Tool definition differs from frozen member catalog"
                        )
                    value = await self.executor.call(name, item["arguments"], child)
                results.append(
                    {"id": item["id"], "name": name, "success": True, "data": value, "error": ""}
                )
            except (InputError, RejectedError) as error:
                results.append(
                    {
                        "id": item["id"],
                        "name": name,
                        "success": False,
                        "data": getattr(error, "data", None),
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
        return results
