"""Immutable host operation definitions, member-scoped registry and invocation."""

import inspect
import json
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal, cast

from tlon.operations.schema import DefinitionError, OperationError, admit_schema, validate
from tlosure.builtins import ARITHMETIC_BUILTINS, CORE_BUILTINS
from tlosure.eval import SPECIAL_FORMS

Effect = Literal["read", "write"]


@dataclass(frozen=True)
class CallContext:
    """Host-created identity, bound outside business arguments."""

    member_id: str
    run_id: str
    call_id: str
    effect_index: int | None = None
    arguments_edn: str | None = None


Handler = Callable[[CallContext, dict[str, Any]], Any]
AsyncHandler = Callable[[CallContext, dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True, init=False)
class Operation:
    """Single schema authority; pure handlers are synchronous, effectful async.

    Schema bytes are immutable. Every public schema projection is a new copy.
    Host assembly supplies scoped handler closures; no global service lookup.
    """

    name: str
    description: str
    effects: frozenset[Effect]
    handler: Handler | AsyncHandler
    retry_safe: bool
    control: bool
    default_timeout: float | None
    _input_json: str
    _output_json: str

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        output_schema: dict[str, Any] | bool,
        handler: Handler | AsyncHandler,
        *,
        effects: frozenset[Effect] = frozenset(),
        retry_safe: bool = False,
        control: bool = False,
        default_timeout: float | None = None,
    ) -> None:
        if len(name) > 64 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
            raise DefinitionError("operation name must be a DSL/Tool identifier")
        if (
            name in {"nil", "true", "false"}
            or name in SPECIAL_FORMS
            or name in CORE_BUILTINS
            or name in ARITHMETIC_BUILTINS
        ):
            raise DefinitionError(f"operation name collides with language vocabulary: {name}")
        if effects - {"read", "write"}:
            raise DefinitionError("unknown operation effect")
        if bool(effects) != inspect.iscoroutinefunction(handler):
            raise DefinitionError("pure handlers must be synchronous; effectful handlers async")
        object.__setattr__(self, "default_timeout", default_timeout)
        object.__setattr__(self, "retry_safe", retry_safe)
        object.__setattr__(self, "control", control)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "handler", handler)
        object.__setattr__(self, "effects", frozenset(effects))
        object.__setattr__(
            self, "_input_json", json.dumps(admit_schema(input_schema, input_schema=True))
        )
        object.__setattr__(self, "_output_json", json.dumps(admit_schema(output_schema)))

    @property
    def input_schema(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self._input_json))

    @property
    def output_schema(self) -> dict[str, Any] | bool:
        return cast(dict[str, Any] | bool, json.loads(self._output_json))


class OperationRegistry:
    """Fixed member-scoped operation catalog; duplicate names are errors."""

    def __init__(self, operations: Iterable[Operation]) -> None:
        self._operations: dict[str, Operation] = {}
        for operation in operations:
            if operation.name in self._operations:
                raise DefinitionError(f"duplicate operation: {operation.name}")
            self._operations[operation.name] = operation

    def get(self, name: str) -> Operation:
        try:
            return self._operations[name]
        except KeyError as error:
            raise OperationError(f"unknown operation: {name}") from error

    def definitions(self) -> tuple[Operation, ...]:
        return tuple(self._operations.values())


class OperationExecutor:
    """Shared input/output boundary, bound to a host-created call context.

    Each invocation may bind its own context without mutating the member scope.
    Domain handlers and their host services own effect submission and persistence.
    """

    def __init__(self, registry: OperationRegistry, context: CallContext) -> None:
        self.registry = registry
        self.context = context

    def call_pure(self, name: str, arguments: Any, context: CallContext | None = None) -> Any:
        operation = self.registry.get(name)
        if operation.effects:
            raise OperationError(f"{name}: effectful operation requires async invocation")
        inputs = validate(operation.input_schema, arguments)
        result = cast(Handler, operation.handler)(self._context(context), inputs)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()
            raise OperationError(f"{name}: pure handler returned an awaitable")
        return validate(operation.output_schema, result, output=True)

    async def call(self, name: str, arguments: Any, context: CallContext | None = None) -> Any:
        operation = self.registry.get(name)
        if not operation.effects:
            return self.call_pure(name, arguments, context)
        inputs = validate(operation.input_schema, arguments)
        handler = cast(AsyncHandler, operation.handler)
        result = await handler(self._context(context), inputs)
        return validate(operation.output_schema, result, output=True)

    def _context(self, context: CallContext | None) -> CallContext:
        bound = context or self.context
        if bound.member_id != self.context.member_id:
            raise OperationError("operation context belongs to another member")
        return bound
