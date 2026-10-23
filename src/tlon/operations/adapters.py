"""Model Tool projection of the shared operation executor."""

from typing import Any

from tlon.operations.core import CallContext, Effect, OperationExecutor
from tlon.operations.schema import InputError, RejectedError
from tlon.tools.base import ToolResult


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

    async def execute_context(
        self, arguments: dict[str, Any], context: CallContext | None = None
    ) -> ToolResult:
        try:
            result = await self._executor.call(self._operation.name, arguments, context)
        except (InputError, RejectedError) as error:
            return ToolResult(
                success=False,
                data=getattr(error, "data", None),
                error=f"{type(error).__name__}: {error}",
            )
        message = result.get("message", "") if isinstance(result, dict) else ""
        return ToolResult(
            success=True, data=result, message=message if isinstance(message, str) else ""
        )
