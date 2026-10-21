"""Thin DSL and Tool projections of the same operation executor."""

from typing import Any

from tlon.base.execution import BudgetExceededError, ExecutionDeadlineError
from tlon.operations.core import CallContext, Effect, OperationExecutor
from tlon.operations.schema import InputError, OperationError, edn_value, json_value, validate
from tlon.tools.base import ToolResult
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

    async def replay(self, call: BuiltinCall, context: CallContext) -> tuple[bool, Any]:
        found, value = await self._executor.replay(call.name, self._arguments(call.args), context)
        return found, edn_value(value) if found else None

    async def handle(self, call: BuiltinCall, context: CallContext | None = None) -> Any:
        """Handle only this adapter's suspended effectful builtins."""
        if self._builtins.get(call.name) is not call.builtin:
            raise OperationError(f"not an operation call from this adapter: {call.name}")
        result = await self._executor.call(call.name, self._arguments(call.args), context)
        return edn_value(result)


class OperationTool:
    """Protocol projection; definitions and execution use the operation authority."""

    def __init__(self, executor: OperationExecutor, name: str) -> None:
        self._executor = executor
        self._operation = executor.registry.get(name)

    @property
    def effects(self) -> frozenset[Effect]:
        return self._operation.effects

    @property
    def definition_id(self) -> str:
        return self._executor.definition_id(self._operation)

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

    @property
    def name(self) -> str:
        return self._operation.name

    @property
    def description(self) -> str:
        return self._operation.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._operation.input_schema

    def get_parameter_description(self) -> str:
        schema = self.parameters
        required = schema.get("required", [])
        lines = []
        for name, field in schema.get("properties", {}).items():
            kind = field.get("type", "JSON") if isinstance(field, dict) else "JSON"
            description = field.get("description", "") if isinstance(field, dict) else ""
            marker = " (required)" if name in required else ""
            lines.append(f"  - {name} ({kind}){marker}: {description}")
        return "\n".join(lines) if lines else "  (no parameters)"

    async def execute_context(
        self, arguments: dict[str, Any], context: CallContext | None = None
    ) -> ToolResult:
        try:
            result = await self._executor.call(self._operation.name, arguments, context)
        except (OperationError, BudgetExceededError, ExecutionDeadlineError) as error:
            return ToolResult(
                success=False,
                data=getattr(error, "data", None),
                error=f"{type(error).__name__}: {error}",
            )
        message = result.get("message", "") if isinstance(result, dict) else ""
        return ToolResult(
            success=True, data=result, message=message if isinstance(message, str) else ""
        )
