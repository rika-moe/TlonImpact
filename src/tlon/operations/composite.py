"""Data-defined operations: closed source, dependency DAG and shared evaluation."""

import hashlib
import json
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import Any, cast

from tlon.operations.core import CallContext, Effect, Operation, OperationExecutor
from tlon.operations.schema import DefinitionError, InputError, OutputError, edn_value, json_value
from tlosure.analysis import referenced_functions
from tlosure.builtins import ARITHMETIC_BUILTINS, CORE_BUILTINS, Builtin, BuiltinCall, EvalError
from tlosure.edn import Form, Keyword, Symbol, dumps, loads
from tlosure.eval import DEFAULT_FUEL, DEFAULT_MAX_DEPTH, Evaluator, current_evaluation
from tlosure.validate import _params

PURE_BUILTINS = {**CORE_BUILTINS, **ARITHMETIC_BUILTINS}


def _result(value: Any) -> Any:
    try:
        return json_value(value, edn_keys=True)
    except InputError as error:
        raise OutputError(f"composite output is not JSON data: {error}") from error


@dataclass(frozen=True, init=False)
class CompositeSpec:
    """Immutable source contract. Examples and approval metadata belong to artifacts."""

    name: str
    description: str
    _input_json: str
    _output_json: str
    _impl_edn: str

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        output_schema: dict[str, Any] | bool,
        impl: Form,
    ) -> None:
        # Reuse the operation boundary's schema/name admission, never a second schema dialect.
        admitted = Operation(name, description, input_schema, output_schema, lambda _c, _a: None)
        if not isinstance(impl, Form) or len(impl) < 3 or impl[0] != Symbol("fn"):
            raise DefinitionError("composite :impl must be a literal (fn [input] body...)")
        try:
            params, rest = _params(impl[1], "fn")
        except EvalError as error:
            raise DefinitionError(str(error)) from error
        if len(params) != 1 or rest is not None:
            raise DefinitionError("composite :impl must take exactly one fixed input parameter")
        object.__setattr__(self, "name", admitted.name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "_input_json", json.dumps(admitted.input_schema))
        object.__setattr__(self, "_output_json", json.dumps(admitted.output_schema))
        object.__setattr__(self, "_impl_edn", dumps(impl))

    @property
    def input_schema(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self._input_json))

    @property
    def output_schema(self) -> dict[str, Any] | bool:
        return cast(dict[str, Any] | bool, json.loads(self._output_json))

    @property
    def impl(self) -> Form:
        return cast(Form, loads(self._impl_edn))

    @classmethod
    def from_edn(cls, text: str) -> "CompositeSpec":
        try:
            value = loads(text)
            if not isinstance(value, dict):
                raise DefinitionError("composite artifact must be a map")
            expected = {"version", "name", "description", "input-schema", "output-schema", "impl"}
            if set(value) != expected:
                raise DefinitionError(f"composite fields must be exactly {sorted(expected)}")
            if type(value["version"]) is not int or value["version"] != 2:
                raise DefinitionError("composite :version must be 2")
            if type(value["name"]) is not str or type(value["description"]) is not str:
                raise DefinitionError("composite name and description must be strings")
            return cls(
                value["name"],
                value["description"],
                json_value(value["input-schema"], edn_keys=True),
                json_value(value["output-schema"], edn_keys=True),
                value["impl"],
            )
        except (ValueError, EvalError) as error:
            if isinstance(error, DefinitionError):
                raise
            raise DefinitionError(f"invalid composite artifact: {error}") from error

    def to_edn(self) -> str:
        return (
            dumps(
                {
                    Keyword("version"): 2,
                    Keyword("name"): self.name,
                    Keyword("description"): self.description,
                    Keyword("input-schema"): self.input_schema,
                    Keyword("output-schema"): self.output_schema,
                    Keyword("impl"): self.impl,
                }
            )
            + "\n"
        )


@dataclass(frozen=True)
class CompositeDefinition:
    spec: CompositeSpec
    dependencies: tuple[str, ...]
    _dependency_json: str
    effects: frozenset[Effect]
    definition_id: str

    @property
    def dependency_ids(self) -> dict[str, str]:
        return cast(dict[str, str], json.loads(self._dependency_json))

    def _evaluator(
        self, executor: OperationExecutor, context: CallContext
    ) -> tuple[Evaluator, int]:
        from tlon.operations.dsl import DSLAdapter

        # A scoped adapter delegates every leaf to the same executor but freezes
        # its nested identity independently of the outer Tool/program entry.
        nested = executor.bind(context)
        vocabulary = {**PURE_BUILTINS, **DSLAdapter(nested).builtins()}
        active = current_evaluation()
        if active is None:
            return Evaluator(vocabulary, DEFAULT_FUEL, DEFAULT_MAX_DEPTH), 0
        return active[0].fork(vocabulary), active[1] + 1

    def _expression(self, inputs: dict[str, Any]) -> Form:
        return Form([self.spec.impl, edn_value(inputs)])

    def evaluate_pure(
        self, executor: OperationExecutor, context: CallContext, inputs: dict[str, Any]
    ) -> Any:
        evaluator, depth = self._evaluator(executor, context)
        return _result(evaluator.eval(self._expression(inputs), {}, depth))

    async def evaluate(
        self, executor: OperationExecutor, context: CallContext, inputs: dict[str, Any]
    ) -> Any:
        from tlon.operations.dsl import DSLAdapter

        evaluator, depth = self._evaluator(executor, context)
        ordinal = 0

        async def dispatch(call: BuiltinCall) -> Any:
            nonlocal ordinal
            ordinal += 1
            path = (context.call_path or (context.call_id,)) + (f"child:{ordinal}:{call.name}",)
            call_id = (
                "nested:"
                + hashlib.sha256(
                    json.dumps([context.run_id, path], separators=(",", ":")).encode()
                ).hexdigest()
            )
            nested = replace(
                context, call_id=call_id, call_path=path, invocation=None, arguments_json=None
            )
            arguments = DSLAdapter._arguments(call.args)
            return edn_value(await executor.call(call.name, arguments, nested))

        return _result(await evaluator.eval_async(self._expression(inputs), {}, depth, dispatch))


@dataclass(frozen=True)
class CompositeCatalog:
    operations: tuple[Operation, ...]
    definitions: dict[str, CompositeDefinition]
    errors: dict[str, str]
    hosts: tuple[Operation, ...]


def compile_composites(
    specs: Iterable[CompositeSpec],
    hosts: Iterable[Operation],
    *,
    strict: bool = True,
) -> CompositeCatalog:
    """Compile independent DAG closures; one invalid closure cannot exclude its peers."""
    host_list = tuple(hosts)
    host_map = {operation.name: operation for operation in host_list}
    if len(host_map) != len(host_list):
        raise DefinitionError("duplicate host operation names")
    source: dict[str, CompositeSpec] = {}
    errors: dict[str, str] = {}
    for spec in specs:
        if spec.name in source or spec.name in host_map or spec.name in PURE_BUILTINS:
            errors[spec.name] = f"operation name collision: {spec.name}"
        source[spec.name] = spec
    vocabulary = {
        **PURE_BUILTINS,
        **{
            name: Builtin(fn=lambda _e, *_a: None, pure=False)
            for name in set(source) | set(host_map)
        },
    }
    dependencies: dict[str, tuple[str, ...]] = {}
    for name, spec in source.items():
        if name in errors:
            continue
        try:
            dependencies[name] = tuple(
                sorted(referenced_functions(spec.impl, vocabulary) - PURE_BUILTINS.keys())
            )
        except EvalError as error:
            errors[name] = str(error)
    operations: dict[str, Operation] = dict(host_map)
    definitions: dict[str, CompositeDefinition] = {}
    visiting: list[str] = []

    def visit(name: str) -> Operation:
        if name in errors:
            raise DefinitionError(f"{name}: {errors[name]}")
        if name in operations:
            return operations[name]
        if name in visiting:
            raise DefinitionError("operation dependency cycle: " + " -> ".join([*visiting, name]))
        visiting.append(name)
        try:
            children = [visit(child) for child in dependencies[name]]
            ids: dict[str, str] = {}
            effects: set[Effect] = set()
            for child in children:
                if child.control:
                    raise DefinitionError(f"{name} cannot wrap control operation {child.name}")
                ids[child.name] = OperationExecutor.definition_id(child)
                if child.composition is not None:
                    ids.update(child.composition.dependency_ids)
                effects.update(child.effects)
            spec = source[name]
            identity = hashlib.sha256(
                json.dumps([spec.to_edn(), ids], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            definition = CompositeDefinition(
                spec,
                dependencies[name],
                json.dumps(ids, sort_keys=True),
                frozenset(effects),
                identity,
            )

            def pure(_context: CallContext, _arguments: dict[str, Any]) -> Any:
                raise AssertionError("composites execute through OperationExecutor")

            async def effect(_context: CallContext, _arguments: dict[str, Any]) -> Any:
                raise AssertionError("composites execute through OperationExecutor")

            result = Operation(
                name,
                spec.description,
                spec.input_schema,
                spec.output_schema,
                effect if effects else pure,
                effects=frozenset(effects),
                composition=definition,
            )
            operations[name] = result
            definitions[name] = definition
            return result
        except (DefinitionError, KeyError) as error:
            errors[name] = str(error)
            raise DefinitionError(f"{name}: {error}") from error
        finally:
            visiting.pop()

    for name in source:
        with suppress(DefinitionError):
            visit(name)
    if strict and errors:
        raise DefinitionError(
            "; ".join(f"{name}: {reason}" for name, reason in sorted(errors.items()))
        )
    return CompositeCatalog(
        tuple(operations[name] for name in definitions), definitions, errors, host_list
    )
