"""Integration-level contract checks for both operation entrances."""

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from tlon.agents.tool_executor import ToolExecutor
from tlon.operations.adapters import DSLAdapter, OperationTool
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.schema import (
    DefinitionError,
    InputError,
    OperationError,
    OutputError,
    json_value,
)
from tlosure.builtins import Builtin, Closure
from tlosure.edn import EDNParseError, Form, Keyword, Symbol, loads
from tlosure.eval import Evaluator


def object_schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def total(_context: CallContext, inputs: dict[str, Any]) -> dict[str, Any]:
    """A real pure matrix aggregation, shared by DSL and Tool calls."""
    return {"total": sum(sum(row) for row in inputs["rows"]), "label": inputs["label"]}


def operation(handler: Any = total) -> Operation:
    return Operation(
        "sum-matrix",
        "Sum a matrix",
        object_schema(
            {
                "rows": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}},
                "label": {"type": ["string", "null"]},
            }
        ),
        object_schema({"total": {"type": "integer"}, "label": {"type": ["string", "null"]}}),
        handler,
    )


def executor(op: Operation | None = None, member: str = "alice") -> OperationExecutor:
    return OperationExecutor(
        OperationRegistry([op or operation()]), CallContext(member, "run-1", "call-1")
    )


def evaluate(adapter: DSLAdapter, inputs: Any) -> Any:
    return Evaluator(adapter.builtins(), 1000, 100).eval(
        Form([Symbol("sum-matrix"), inputs]), {}, 0
    )


async def test_pure_operation_same_definition_two_real_entrances() -> None:
    execution = executor()
    tools = ToolExecutor()
    tool = OperationTool(execution, "sum-matrix")
    tools.register_tool(tool)
    inputs = {"rows": [[1, 2], [3]], "label": None}
    assert json_value(evaluate(DSLAdapter(execution), inputs), edn_keys=True) == {
        "total": 6,
        "label": None,
    }
    result = await tools.execute("sum-matrix", inputs)
    assert result.success and result.data == {"total": 6, "label": None}
    assert tool.to_openai_definition()["function"]["parameters"] == operation().input_schema
    assert DSLAdapter(execution).builtins()["sum-matrix"].pure


@pytest.mark.parametrize(
    "inputs",
    [
        {"rows": [[True]], "label": None},
        {"rows": [["bad"]], "label": None},
        {"rows": [1], "label": None},
        {"rows": [[1]], "label": None, "extra": 1},
        {"rows": [[1]], "label": 5},
        {"rows": [[1]]},
        {"rows": [[1]], "label": None, "member_id": "mallory"},
        {"rows": [[1]], "label": None, "run_id": "forged"},
    ],
)
async def test_both_entrances_reject_same_invalid_inputs(inputs: dict[str, Any]) -> None:
    execution = executor()
    with pytest.raises(InputError):
        evaluate(DSLAdapter(execution), inputs)
    tools = ToolExecutor()
    tools.register_tool(OperationTool(execution, "sum-matrix"))
    result = await tools.execute("sum-matrix", inputs)
    assert not result.success and "InputError" in result.error


@pytest.mark.parametrize(
    "value",
    [
        Symbol("s"),
        Keyword("k"),
        Form([1]),
        Closure([], None, [], {}),
        float("nan"),
        float("inf"),
        (1,),
        {Symbol("key"): 1},
    ],
)
def test_code_and_non_json_values_rejected(value: Any) -> None:
    with pytest.raises(InputError):
        json_value({"nested": [value]}, edn_keys=True)


@pytest.mark.parametrize("source", ['{:to 1 "to" 2}', '{"to" 1 :to 2}', "{to 1 :to 2}"])
def test_parser_rejects_cross_type_key_collision_before_data_loss(source: str) -> None:
    with pytest.raises(EDNParseError, match="collide"):
        loads(source)


def test_edn_spelling_projection_and_same_type_duplicate_semantics() -> None:
    assert json_value(loads("{:thread-id {:snake_key [1]}}"), edn_keys=True) == {
        "thread-id": {"snake_key": [1]}
    }
    assert loads("{:a 1 :a 2}") == {Keyword("a"): 2}
    with pytest.raises(InputError):
        json_value({Keyword("a"): 1})


async def test_bad_output_rejected_from_both_entrances() -> None:
    execution = executor(operation(lambda _context, _inputs: {"total": True, "label": None}))
    with pytest.raises(OutputError):
        evaluate(DSLAdapter(execution), {"rows": [], "label": None})
    tools = ToolExecutor()
    tools.register_tool(OperationTool(execution, "sum-matrix"))
    result = await tools.execute("sum-matrix", {"rows": [], "label": None})
    assert not result.success and "OutputError" in result.error


async def test_scoped_ports_and_host_identity_are_isolated() -> None:
    seen: list[CallContext] = []

    def scoped(member: str) -> OperationExecutor:
        private_store = {"owner": member}

        async def read(context: CallContext, _inputs: dict[str, Any]) -> Any:
            seen.append(context)
            return private_store

        return executor(
            Operation(
                "read-owner",
                "Read private owner",
                object_schema({}),
                object_schema({"owner": {"type": "string"}}),
                read,
                effects=frozenset({"read"}),
            ),
            member,
        )

    for member in ("alice", "bob"):
        execution = scoped(member)
        adapter = DSLAdapter(execution)
        assert not adapter.builtins()["read-owner"].pure
        # Even an expression environment with forged identity is irrelevant.
        result = await Evaluator(adapter.builtins(), 100, 100).eval_async(
            loads("(read-owner {})"), {"member_id": "mallory", "run_id": "bad"}, 0, adapter.handle
        )
        assert json_value(result, edn_keys=True) == {"owner": member}
        tools = ToolExecutor()
        tools.register_tool(OperationTool(execution, "read-owner"))
        assert (await tools.execute("read-owner", {})).data == {"owner": member}
        assert not (await tools.execute("read-owner", {"member_id": "mallory"})).success
    assert [(context.member_id, context.run_id) for context in seen] == [("alice", "run-1")] * 2 + [
        ("bob", "run-1")
    ] * 2


async def test_schema_and_projection_mutation_cannot_change_contract() -> None:
    inputs = object_schema({"x": {"type": "integer"}})
    outputs = {"type": "integer"}
    op = Operation("identity", "identity", inputs, outputs, lambda _ctx, args: args["x"])
    inputs["properties"]["x"]["type"] = "string"
    outputs["type"] = "string"
    op.input_schema["properties"].clear()
    execution = executor(op)
    tool = OperationTool(execution, "identity")
    tool.parameters["properties"].clear()
    tool.to_openai_definition()["function"]["parameters"]["properties"].clear()
    assert (await tool.execute_context({"x": 7})).data == 7
    assert not (await tool.execute_context({"x": "bad"})).success
    with pytest.raises(FrozenInstanceError):
        op.name = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        execution.context.member_id = "changed"  # type: ignore[misc]


async def test_handler_cannot_mutate_callers_arguments_or_returned_data() -> None:
    retained = {"rows": [[1]], "label": None}

    def mutate(_context: CallContext, inputs: dict[str, Any]) -> Any:
        inputs["rows"][0].append(10)
        return retained

    op = Operation(
        "mutate", "test isolation", operation().input_schema, operation().input_schema, mutate
    )
    arguments = {"rows": [[2]], "label": None}
    result = await executor(op).call("mutate", arguments)
    result["rows"][0].append(20)
    assert arguments == {"rows": [[2]], "label": None}
    assert retained == {"rows": [[1]], "label": None}


@pytest.mark.parametrize(
    "keyword,value",
    [
        ("$ref", "https://example.org/schema"),
        ("default", 1),
        ("format", "email"),
        ("anyOf", []),
        ("pattern", "a"),
    ],
)
def test_unsupported_schema_keywords_rejected(keyword: str, value: Any) -> None:
    with pytest.raises(DefinitionError, match="unsupported"):
        Operation("bad", "bad", object_schema({}), {"type": "string", keyword: value}, total)


def test_invalid_schema_and_duplicate_definitions_rejected() -> None:
    with pytest.raises(DefinitionError):
        Operation("bad", "bad", {"type": "object"}, {"type": "string"}, total)
    with pytest.raises(DefinitionError):
        Operation("bad", "bad", object_schema({}), {"type": "string", "minLength": -1}, total)
    with pytest.raises(DefinitionError, match="duplicate"):
        OperationRegistry([operation(), operation()])
    with pytest.raises(OperationError, match="unknown"):
        executor().call_pure("absent", {})


async def test_async_pure_handler_and_sync_effect_handler_not_admitted() -> None:
    async def async_handler(_context: CallContext, _inputs: dict[str, Any]) -> int:
        return 1

    with pytest.raises(DefinitionError, match="synchronous"):
        Operation("bad", "bad", object_schema({}), {"type": "integer"}, async_handler)
    with pytest.raises(DefinitionError, match="async"):
        Operation(
            "bad", "bad", object_schema({}), {"type": "integer"}, total, effects=frozenset({"read"})
        )
    with pytest.raises(InputError, match="exactly one"):
        Evaluator(DSLAdapter(executor()).builtins(), 100, 100).eval(
            loads("(sum-matrix {} {})"), {}, 0
        )


async def test_dsl_handler_rejects_foreign_registry_calls() -> None:
    from tlosure.builtins import BuiltinCall

    adapter = DSLAdapter(executor())
    with pytest.raises(OperationError, match="not an operation"):
        await adapter.handle(
            BuiltinCall("sum-matrix", Builtin(fn=lambda _env: None, pure=False), [{}], {})
        )


@pytest.mark.parametrize(
    "schema",
    [
        [],
        {"type": [{}]},
        {"type": "object", "properties": {}, "required": 1, "additionalProperties": False},
    ],
)
def test_malformed_schemas_report_definition_error(schema: Any) -> None:
    with pytest.raises(DefinitionError):
        Operation("invalid", "invalid", schema, {"type": "string"}, total)


@pytest.mark.parametrize("name", ["if", "map", "inc", "get", "bad/name", "1abc", "bad name", ""])
def test_names_fit_both_entrances_without_language_collision(name: str) -> None:
    with pytest.raises(DefinitionError):
        Operation(name, "bad", object_schema({}), {"type": "integer"}, total)


def test_cyclic_values_rejected_and_reused_noncyclic_values_accepted() -> None:
    cycle: list[Any] = []
    cycle.append(cycle)
    with pytest.raises(InputError, match="cyclic"):
        json_value(cycle)
    reused = [1]
    assert json_value([reused, reused]) == [[1], [1]]
    deep: Any = 1
    for _ in range(102):
        deep = [deep]
    with pytest.raises(InputError, match="nesting"):
        json_value(deep)


async def test_pure_handler_cannot_leak_awaitable() -> None:
    async def result() -> int:
        return 1

    execution = executor(
        Operation(
            "leak", "bad host", object_schema({}), {"type": "integer"}, lambda _ctx, _args: result()
        )
    )
    with pytest.raises(OperationError, match="awaitable"):
        execution.call_pure("leak", {})


@pytest.mark.parametrize("value", [float("nan"), Form([1]), {"total": 1, "label": Symbol("s")}])
async def test_invalid_output_data_never_escapes(value: Any) -> None:
    execution = executor(operation(lambda _context, _inputs: value))
    with pytest.raises(OutputError):
        await execution.call("sum-matrix", {"rows": [], "label": None})


async def test_self_is_a_valid_business_parameter_on_both_entrances() -> None:
    op = Operation(
        "echo-self",
        "Echo input",
        object_schema({"self": {"type": "string"}}),
        {"type": "string"},
        lambda _context, inputs: inputs["self"],
    )
    execution = executor(op)
    adapter = DSLAdapter(execution)
    assert (
        Evaluator(adapter.builtins(), 100, 100).eval(loads('(echo-self {:self "value"})'), {}, 0)
        == "value"
    )
    tools = ToolExecutor()
    tools.register_tool(OperationTool(execution, "echo-self"))
    result = await tools.execute("echo-self", {"self": "value"})
    assert result.success and result.data == "value"


async def test_tool_logging_cannot_bypass_argument_boundary() -> None:
    tools = ToolExecutor()
    tools.register_tool(OperationTool(executor(), "sum-matrix"))
    cycle: list[Any] = []
    cycle.append(cycle)
    for inputs in ({"rows": cycle, "label": None}, {"rows": [{object(): 1}], "label": None}):
        result = await tools.execute("sum-matrix", inputs)
        assert not result.success and "InputError" in result.error


async def test_supported_scalar_bounds_enum_and_array_constraints() -> None:
    schema = object_schema(
        {
            "items": {
                "type": "array",
                "items": {"type": "number", "minimum": 0, "maximum": 10},
                "minItems": 1,
                "maxItems": 2,
                "uniqueItems": True,
            },
            "label": {"type": "string", "minLength": 1, "maxLength": 3, "enum": ["ok", "yes"]},
        }
    )
    execution = executor(
        Operation("bounded", "bounded", schema, schema, lambda _context, inputs: inputs)
    )
    assert await execution.call("bounded", {"items": [1.5, 2], "label": "ok"}) == {
        "items": [1.5, 2],
        "label": "ok",
    }
    for items, label in [
        ([], "ok"),
        ([1, 1], "ok"),
        ([11], "ok"),
        ([True], "ok"),
        ([1], "no"),
        ([1, 2, 3], "ok"),
    ]:
        with pytest.raises(InputError):
            await execution.call("bounded", {"items": items, "label": label})


@pytest.mark.parametrize(
    "source", ['(assoc {:a 1} "a" 2)', '(conj {:a 1} ["a" 2])', '(assoc {} :a 1 "a" 2)']
)
def test_dsl_map_construction_rejects_collisions_before_operation_boundary(source: str) -> None:
    from tlosure.builtins import CORE_BUILTINS
    from tlosure.eval import EvalError

    adapter = DSLAdapter(executor())
    builtins = {**CORE_BUILTINS, **adapter.builtins()}
    with pytest.raises(EvalError, match="collide"):
        Evaluator(builtins, 100, 100).eval(loads(f"(sum-matrix {source})"), {}, 0)


@pytest.mark.parametrize("name", ["nil", "true", "false", "a" * 65])
def test_literal_or_overlong_operation_names_rejected(name: str) -> None:
    with pytest.raises(DefinitionError):
        Operation(name, "bad", object_schema({}), {"type": "integer"}, total)
