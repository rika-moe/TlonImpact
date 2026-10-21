"""Every condition projects the same operations over explicit materialized data."""

from pathlib import Path
from typing import Any

import pytest

from tlon.agents.vocabulary import (
    LOOP_SCOPE,
    RULE_SCOPE,
    STAGE_SCOPE,
    condition_builtins,
    evaluate_condition,
    make_member_builtins,
    validate_condition,
)
from tlon.operations.composite import CompositeSpec, compile_composites
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.definitions import host_operation_definitions
from tlon.operations.dsl import DSLAdapter
from tlon.operations.schema import InputError
from tlosure.builtins import Builtin, EvalError
from tlosure.edn import Form, loads

CONTEXTS = [
    (RULE_SCOPE, "event", {"msg": None, "self": {}}),
    (LOOP_SCOPE, "state", {"self": {}, "limits": {}}),
    (STAGE_SCOPE, "scratch", {"state": {}, "round": {}}),
]


def composite(name: str, body: str) -> CompositeSpec:
    impl = loads(f"(fn [input] {body})")
    assert isinstance(impl, Form)
    return CompositeSpec(
        name,
        name,
        {
            "type": "object",
            "properties": {"facts": {"type": "array", "items": {"type": "string"}}},
            "required": ["facts"],
            "additionalProperties": False,
        },
        {"type": "boolean"},
        impl,
    )


@pytest.mark.parametrize("scope,binding,other", CONTEXTS)
@pytest.mark.parametrize("facts,expected", [([], False), (["actionable"], True)])
def test_same_schema_checked_composite_in_every_condition(
    scope: frozenset[str], binding: str, other: dict[str, Any], facts: list[str], expected: bool
) -> None:
    catalog = compile_composites(
        [composite("has-actionable-facts", "(> (count (get input :facts)) 0)")], []
    )
    executor = OperationExecutor(
        OperationRegistry(catalog.operations), CallContext("member", "root", "condition")
    )
    registry = {**make_member_builtins(), **DSLAdapter(executor).builtins()}
    expression = loads(f"(has-actionable-facts {binding})")
    validate_condition(expression, registry, scope)
    assert (
        evaluate_condition(expression, {**other, binding: {"facts": facts}}, registry, scope)
        is expected
    )
    with pytest.raises(InputError):
        evaluate_condition(expression, {**other, binding: {}}, registry, scope)


@pytest.mark.parametrize("scope,binding,other", CONTEXTS)
@pytest.mark.parametrize(
    "call",
    [
        '(sleeping? "someone")',
        '(running? "someone")',
        '(lazy? "someone")',
        "(member-roster)",
        '(model-call {:prompt "hello"})',
        '(mail-send {:to ["someone@tlon.internal"] :content "hello"})',
        '(state-put {:key "key" :value true})',
        "(clock-read)",
    ],
)
def test_local_function_cannot_hide_external_queries_or_effects(
    scope: frozenset[str], binding: str, other: dict[str, Any], call: str
) -> None:
    registry = {
        **make_member_builtins(),
        "sleeping?": Builtin(lambda _env, _who: False, pure=False),
        "running?": Builtin(lambda _env, _who: False, pure=False),
        "lazy?": Builtin(lambda _env, _who: False, pure=False),
        "member-roster": Builtin(lambda _env: [], pure=False),
        "clock-read": Builtin(lambda _env: 123, pure=False),
    }
    expression = loads(f"(let [hidden (fn [] {call})] (hidden))")
    with pytest.raises(EvalError, match="unknown function"):
        validate_condition(expression, registry, scope)
    with pytest.raises(EvalError, match="unknown function"):
        evaluate_condition(expression, {**other, binding: {}}, registry, scope)


@pytest.mark.parametrize("scope,binding,other", CONTEXTS)
def test_transitive_read_effect_cannot_be_called_as_a_pure_condition(
    scope: frozenset[str], binding: str, other: dict[str, Any]
) -> None:
    async def read(_context: CallContext, _inputs: dict[str, Any]) -> bool:
        raise AssertionError("condition must never dispatch the host query")

    host = Operation(
        "external-query",
        "query",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        {"type": "boolean"},
        read,
        effects=frozenset({"read"}),
    )
    catalog = compile_composites(
        [
            composite("read-wrapper", "(external-query {})"),
            composite("indirect-wrapper", "(read-wrapper input)"),
        ],
        [host],
    )
    executor = OperationExecutor(
        OperationRegistry((host, *catalog.operations)), CallContext("member", "root", "condition")
    )
    registry = {**make_member_builtins(), **DSLAdapter(executor).builtins()}
    assert "indirect-wrapper" not in condition_builtins(registry)
    with pytest.raises(EvalError, match="unknown function"):
        evaluate_condition(
            loads("(indirect-wrapper {:facts []})"), {**other, binding: {}}, registry, scope
        )


@pytest.mark.parametrize("scope,binding,other", CONTEXTS)
def test_condition_scope_missing_at_load_and_execution_is_explicit(
    scope: frozenset[str], binding: str, other: dict[str, Any]
) -> None:
    registry = make_member_builtins()
    with pytest.raises(EvalError, match="undefined symbol"):
        validate_condition(loads("(get nonexistent :facts)"), registry, scope)
    with pytest.raises(EvalError, match="missing bindings"):
        evaluate_condition(True, other, registry, scope)
    with pytest.raises(EvalError, match="materialized data"):
        evaluate_condition(True, {**other, binding: object()}, registry, scope)


def test_catalog_projection_does_not_promote_external_reads_to_pure() -> None:
    adapter = DSLAdapter(
        OperationExecutor(
            OperationRegistry(host_operation_definitions()),
            CallContext("member", "root", "condition"),
        )
    )
    assert all(not word.pure for word in adapter.builtins().values())


async def test_composite_reaches_program_and_both_loop_condition_sites(tmp_path: Path) -> None:
    from dataclasses import replace
    from unittest.mock import AsyncMock

    from tests.agents.test_completion_gate import _make_driver
    from tlon.agents.member_spec import MemberSpec, validate_member_spec
    from tlon.agents.program_driver import ProgramDriver
    from tlon.agents.program_store import ProgramStore
    from tlon.agents.session import AgentSession

    catalog = compile_composites(
        [composite("has-actionable-facts", "(> (count (get input :facts)) 0)")], []
    )
    executor = OperationExecutor(
        OperationRegistry(catalog.operations), CallContext("member", "root", "condition")
    )
    registry = {**make_member_builtins(), **DSLAdapter(executor).builtins()}
    spec = MemberSpec.from_edn(
        loads(
            '{:version 1 :rules [{:name "test" :on :mail-delivered '
            ":when (has-actionable-facts event) :actions [(record-hit)]}]}"
        )
    )
    calls: list[bool] = []
    registry["record-hit"] = Builtin(lambda _env: calls.append(True), pure=False)
    validate_member_spec(spec, builtins=registry)
    await ProgramDriver(ProgramStore(None, build_id="test"), "root", registry, AsyncMock()).run(
        spec.rules[0], {"msg": None, "event": {"facts": ["task"]}, "self": {}}
    )
    assert calls == [True]

    driver, *_ = _make_driver(tmp_path, [])
    driver._condition_registry = registry
    driver._loop = replace(
        driver._loop, until=loads("(has-actionable-facts {:facts [(str (get state :iteration))]})")
    )
    session = AgentSession(session_id="pure-conditions", trigger_content="task")
    assert driver._until_reached(session) is True
    for facts, expected in [([], False), (["task"], True)]:
        session.loop_scratch["facts"] = facts
        assert (
            driver._stage_condition_met(
                session, "act", {"if": loads("(has-actionable-facts scratch)")}, None
            )
            is expected
        )


def test_dynamic_recipe_validation_uses_active_pure_composite_registry() -> None:
    from tlosure.eval import eval_form

    catalog = compile_composites(
        [composite("has-actionable-facts", "(> (count (get input :facts)) 0)")], []
    )
    executor = OperationExecutor(
        OperationRegistry(catalog.operations), CallContext("member", "root", "condition")
    )
    collector: list[Any] = []
    registry = {
        **make_member_builtins(collector=collector),
        **DSLAdapter(executor).builtins(),
    }
    recipe = (
        "{:context {:sections [(persona)]} "
        ':stages [(think {:if (has-actionable-facts {:facts ["stage"]})}) (act)] '
        ":until (has-actionable-facts {:facts [(str (get state :iteration))]}) "
        ":max-iterations 2 :max-consecutive-errors 2 :max-tokens 1000 "
        ":reflect {:enabled false :interval-minutes 5}}"
    )
    request = eval_form(loads(f"(let [recipe (quote {recipe})] (run-loop recipe))"), {}, registry)
    assert request.kind == "run_loop"
    assert collector == [request]


def test_rule_context_materializes_mail_and_event_without_mutating_delivery() -> None:
    from tlon.agents.vocabulary import rule_env
    from tlon.organization.mail.mail import Mail

    mail = Mail.create(
        from_address="sender@tlon.internal", to=["member@tlon.internal"], content="task"
    )
    payload = {"mail": mail, "facts": ["task"]}
    env = rule_env(payload, member_id="member", name="Member")
    assert env["msg"] == mail.to_dict()
    assert env["event"]["mail"] == mail.to_dict()
    assert payload["mail"] is mail
    env["event"]["facts"].append("local-only")
    assert payload["facts"] == ["task"]
    assert (
        evaluate_condition(
            loads('(from? msg "sender@tlon.internal")'),
            dict(env),
            make_member_builtins(),
            RULE_SCOPE,
        )
        is True
    )


def test_frozen_session_conditions_keep_original_composite_after_catalog_change(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from tests.agents.test_completion_gate import _make_driver
    from tlon.agents.program_store import ProgramStore
    from tlon.agents.session import AgentSession

    store = ProgramStore(tmp_path / "conditions.sqlite", build_id="stable-host")

    def execution(body: str) -> OperationExecutor:
        catalog = compile_composites([composite("has-actionable-facts", body)], [])
        return OperationExecutor(
            OperationRegistry(catalog.operations),
            CallContext("member", "root-A", "condition"),
            store,
        )

    original = execution("true")
    store.freeze_definitions("root-A", original.definition_snapshot())
    current = execution("false")
    old_root = current.for_root("root-A")
    new_root = current.for_root("root-B")
    driver, *_ = _make_driver(tmp_path, [])
    expression = loads("(has-actionable-facts {:facts []})")
    driver._loop = replace(driver._loop, until=expression)
    session = AgentSession(session_id="frozen-conditions", trigger_content="task")
    for root, expected in [(old_root, True), (new_root, False)]:
        driver._condition_registry = {**make_member_builtins(), **DSLAdapter(root).builtins()}
        assert driver._until_reached(session) is expected
        assert driver._stage_condition_met(session, "act", {"if": expression}, None) is expected


async def test_when_and_actions_consume_one_shared_fuel_budget() -> None:
    from dataclasses import replace
    from unittest.mock import AsyncMock

    from tlon.agents.member_spec import MemberSpec
    from tlon.agents.program_driver import ProgramDriver
    from tlon.agents.program_store import ProgramStore
    from tlosure.builtins import FuelExhaustedError
    from tlosure.eval import eval_form

    registry = make_member_builtins()
    spec = MemberSpec.from_edn(
        loads(
            '{:version 1 :rules [{:name "fuel" :on :mail-delivered '
            ":when (= 1 1) :fuel 3 :actions [(+ 1 1)]}]}"
        )
    )
    rule = spec.rules[0]
    # Each half fits the cap independently. Sharing the cap must fail before the action completes.
    assert eval_form(rule.when, {}, registry, fuel=3) is True
    assert eval_form(rule.actions[0], {}, registry, fuel=3) == 2
    store = ProgramStore(None, build_id="test")
    with pytest.raises(FuelExhaustedError):
        await ProgramDriver(store, "shared", registry, AsyncMock()).run(
            rule, {"msg": None, "event": {}, "self": {}}
        )
    await ProgramDriver(store, "enough", registry, AsyncMock()).run(
        replace(rule, fuel=6), {"msg": None, "event": {}, "self": {}}
    )


@pytest.mark.parametrize("scope,binding,other", CONTEXTS)
def test_control_metadata_alone_excludes_operation_from_every_condition(
    scope: frozenset[str], binding: str, other: dict[str, Any]
) -> None:
    def forbidden(_context: CallContext, _inputs: dict[str, Any]) -> bool:
        raise AssertionError("a control operation cannot run inside a condition")

    control = Operation(
        "custom-transition",
        "control without a write effect",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        {"type": "boolean"},
        forbidden,
        control=True,
    )
    executor = OperationExecutor(
        OperationRegistry([control]), CallContext("member", "root", "condition")
    )
    registry = {**make_member_builtins(), **DSLAdapter(executor).builtins()}
    assert not registry["custom-transition"].pure
    with pytest.raises(EvalError, match="unknown function"):
        evaluate_condition(loads("(custom-transition {})"), {**other, binding: {}}, registry, scope)
