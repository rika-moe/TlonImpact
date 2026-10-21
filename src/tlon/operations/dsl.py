"""Language projection of the shared operation executor, independent of Tools."""

from typing import Any

from tlon.operations.core import CallContext, OperationExecutor
from tlon.operations.schema import InputError, OperationError, edn_value, json_value, validate
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

    def control_names(self) -> frozenset[str]:
        return frozenset(op.name for op in self._executor.registry.definitions() if op.control)

    def owns(self, call: BuiltinCall) -> bool:
        return self._builtins.get(call.name) is call.builtin

    def prepare(self, call: BuiltinCall) -> None:
        """Validate arguments before durable dispatch."""
        operation = self._executor.registry.get(call.name)
        validate(operation.input_schema, self._arguments(call.args))

    async def replay(self, call: BuiltinCall, context: CallContext) -> tuple[bool, Any]:
        found, value = await self._executor.replay(call.name, self._arguments(call.args), context)
        return found, edn_value(value) if found else None

    async def handle(self, call: BuiltinCall, context: CallContext | None = None) -> Any:
        """Handle only this adapter's suspended effectful builtins."""
        if self._builtins.get(call.name) is not call.builtin:
            raise OperationError(f"not an operation call from this adapter: {call.name}")
        result = await self._executor.call(call.name, self._arguments(call.args), context)
        return edn_value(result)
