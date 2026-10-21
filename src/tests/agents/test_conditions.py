"""Every condition projects the same operations over explicit materialized data."""

from typing import Any

import pytest

from tlon.agents.vocabulary import (
    LOOP_SCOPE,
    RULE_SCOPE,
    STAGE_SCOPE,
    evaluate_condition,
    make_member_builtins,
    validate_condition,
)
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlosure.builtins import Builtin, EvalError
from tlosure.edn import loads

CONTEXTS = [
    (RULE_SCOPE, "event", {"msg": None, "self": {}}),
    (LOOP_SCOPE, "state", {"self": {}, "limits": {}}),
    (STAGE_SCOPE, "scratch", {"state": {}, "round": {}}),
]


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
