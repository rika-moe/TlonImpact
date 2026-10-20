"""Thin DSL and Tool projections of the same operation executor."""

from typing import Any

from tlon.operations.core import CallContext, OperationExecutor
from tlon.operations.schema import InputError, OperationError, edn_value, json_value, validate
from tlon.tools.base import Tool, ToolResult
from tlosure.builtins import Builtin, BuiltinCall


class DSLAdapter:
    """Bind identity in host closures, never in the user expression environment."""

    def __init__(self, executor: OperationExecutor) -> None:
        self._executor = executor
        self._builtins = {
            operation.name: self._builtin(operation.name, pure=not operation.effects)
            for operation in executor.registry.definitions()
        }

    def _builtin(self, name: str, *, pure: bool) -> Builtin:
        def invoke(_env: dict[str, Any], *args: Any) -> Any:
            inputs = self._arguments(args)
            return edn_value(self._executor.call_pure(name, inputs))

        return Builtin(fn=invoke, pure=pure)

    @staticmethod
    def _arguments(args: tuple[Any, ...] | list[Any]) -> Any:
        if len(args) != 1:
            raise InputError("operation expects exactly one named argument object")
        return json_value(args[0], edn_keys=True)

    def builtins(self) -> dict[str, Builtin]:
        return dict(self._builtins)

    def owns(self, call: BuiltinCall) -> bool:
        return self._builtins.get(call.name) is call.builtin

    def prepare(self, call: BuiltinCall) -> bool:
        """Validate before durable dispatch; return whether replay is unsafe."""
        operation = self._executor.registry.get(call.name)
        validate(operation.input_schema, self._arguments(call.args))
        return "write" in operation.effects and not operation.retry_safe

    async def handle(self, call: BuiltinCall, context: CallContext | None = None) -> Any:
        """Handle only this adapter's suspended effectful builtins."""
        if self._builtins.get(call.name) is not call.builtin:
            raise OperationError(f"not an operation call from this adapter: {call.name}")
        result = await self._executor.call(call.name, self._arguments(call.args), context)
        return edn_value(result)


class OperationTool(Tool):
    """Protocol projection; definitions and execution use the operation authority."""

    def __init__(self, executor: OperationExecutor, name: str) -> None:
        self._executor = executor
        self._operation = executor.registry.get(name)
        super().__init__(name, self._operation.description, self._operation.input_schema)

    @property
    def default_timeout(self) -> float | None:
        return self._operation.default_timeout

    def to_openai_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self._operation.name,
                "description": self._operation.description,
                "parameters": self._operation.input_schema,
            },
        }

    def validate_arguments(self, arguments: dict[str, Any]) -> list[str]:
        """Defer once to the shared boundary; bypass the legacy shallow validator."""
        return []

    def recovery_metadata(self, arguments: dict[str, Any], context: CallContext) -> dict[str, Any]:
        if "write" not in self._operation.effects:
            return {}
        if not self._operation.retry_safe:
            return {"call_id": context.call_id, "outcome": "unknown", "retry_safe": False}
        if "request_id" not in self._operation.input_schema["properties"]:
            return {"call_id": context.call_id, "outcome": "unknown", "retry_safe": True}
        return {"request_id": arguments.get("request_id") or context.call_id, "outcome": "unknown"}

    def timeout_result(self, arguments: dict[str, Any], context: CallContext) -> ToolResult:
        data = self.recovery_metadata(arguments, context)
        return ToolResult(
            success=False,
            data=data,
            error="Operation interrupted; outcome unknown" if data else "Operation interrupted",
        )

    async def execute(self, /, **kwargs: Any) -> ToolResult:
        return await self.execute_context(kwargs)

    async def execute_context(
        self, arguments: dict[str, Any], context: CallContext | None = None
    ) -> ToolResult:
        try:
            result = await self._executor.call(self._operation.name, arguments, context)
        except OperationError as error:
            return ToolResult(
                success=False,
                data=getattr(error, "data", None),
                error=f"{type(error).__name__}: {error}",
            )
        message = result.get("message", "") if isinstance(result, dict) else ""
        return ToolResult(
            success=True, data=result, message=message if isinstance(message, str) else ""
        )
