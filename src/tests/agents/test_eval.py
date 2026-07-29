"""Tests for the restricted evaluator (tlon.agents.eval) and trigger expressions."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tlon.agents.edn import Form, Keyword, loads
from tlon.agents.eval import (
    TRIGGER_BUILTINS,
    Builtin,
    EvalDepthError,
    EvalError,
    FuelExhaustedError,
    eval_form,
    validate_form,
)
from tlon.agents.spec import AgentSpec, SpecValidationError, TriggerSpec
from tlon.agents.trigger import TriggerEvaluator
from tlon.organization.mail.mail import Mail


def _mail(
    content: str = "hello",
    to: list[str] | None = None,
    cc: list[str] | None = None,
    from_address: str = "u-1@tlon.internal",
) -> Mail:
    return Mail.create(
        from_address=from_address,
        to=to or ["someone@tlon.internal"],
        cc=cc,
        content=content,
    )


_SELF = {"id": "a-001", "name": "小特"}
_SELF_ADDRESS = "a-001@tlon.internal"


def _env(msg: Mail) -> dict:
    return {"msg": msg, "self": _SELF}


class TestLiterals:
    def test_self_evaluating(self):
        assert eval_form(loads("42"), {}, {}) == 42
        assert eval_form(loads("3.14"), {}, {}) == 3.14
        assert eval_form(loads('"s"'), {}, {}) == "s"
        assert eval_form(loads("true"), {}, {}) is True
        assert eval_form(loads("nil"), {}, {}) is None
        assert eval_form(loads(":kw"), {}, {}) == Keyword("kw")

    def test_symbol_lookup(self):
        assert eval_form(loads("x"), {"x": 7}, {}) == 7

    def test_undefined_symbol(self):
        with pytest.raises(EvalError, match="undefined symbol: x"):
            eval_form(loads("x"), {}, {})

    def test_vector_evaluates_items(self):
        assert eval_form(loads("[(= 1 1) 2 x]"), {"x": 3}, TRIGGER_BUILTINS) == [True, 2, 3]


class TestSpecialForms:
    def test_if_three_branches(self):
        assert eval_form(loads("(if true 1 2)"), {}, {}) == 1
        assert eval_form(loads("(if false 1 2)"), {}, {}) == 2

    def test_if_without_else_returns_nil(self):
        assert eval_form(loads("(if false 1)"), {}, {}) is None

    def test_if_arity_error(self):
        with pytest.raises(EvalError, match="if expects 2 or 3 arguments"):
            eval_form(loads("(if true)"), {}, {})

    def test_when(self):
        assert eval_form(loads("(when false 1)"), {}, {}) is None
        assert eval_form(loads("(when true 1 2)"), {}, {}) == 2

    def test_let_sequential_bindings(self):
        # y sees the earlier binding of x
        assert eval_form(loads("(let [x 1 y x] y)"), {}, {}) == 1
        # later binding shadows earlier
        assert eval_form(loads("(let [x 1 x 2] x)"), {}, {}) == 2

    def test_let_requires_vector_bindings(self):
        with pytest.raises(EvalError, match="let bindings must be a vector"):
            eval_form(loads("(let (x 1) x)"), {}, {})
        with pytest.raises(EvalError, match="even number of forms"):
            eval_form(loads("(let [x] x)"), {}, {})

    def test_and_or_return_values(self):
        assert eval_form(loads("(and 1 2)"), {}, {}) == 2
        assert eval_form(loads("(and 1 nil 3)"), {}, {}) is None
        assert eval_form(loads("(or nil 5)"), {}, {}) == 5
        assert eval_form(loads("(or nil false)"), {}, {}) is False
        assert eval_form(loads("(and)"), {}, {}) is True
        assert eval_form(loads("(or)"), {}, {}) is None

    def test_and_short_circuits(self):
        calls: list[int] = []

        def mark(env: dict, value: int) -> int:
            calls.append(value)
            return value

        builtins = {**TRIGGER_BUILTINS, "mark": Builtin(mark)}
        assert eval_form(loads("(and false (mark 1))"), {}, builtins) is False
        assert calls == []

    def test_or_short_circuits(self):
        calls: list[int] = []

        def mark(env: dict, value: int) -> int:
            calls.append(value)
            return value

        builtins = {**TRIGGER_BUILTINS, "mark": Builtin(mark)}
        assert eval_form(loads("(or true (mark 1))"), {}, builtins) is True
        assert calls == []


class TestErrors:
    def test_unknown_function(self):
        with pytest.raises(EvalError, match="unknown function: frobnicate"):
            eval_form(loads("(frobnicate 1)"), {}, TRIGGER_BUILTINS)

    def test_empty_form(self):
        with pytest.raises(EvalError, match="empty form"):
            eval_form(loads("()"), {}, {})

    def test_non_symbol_head(self):
        with pytest.raises(EvalError, match="form head must be a symbol"):
            eval_form(loads("(1 2)"), {}, {})

    def test_no_recursion_possible(self):
        # def/lambda/loop/recur are not special forms nor builtins
        for source in ("(def x 1)", "(lambda (x) x)", "(loop [x 1] x)", "(recur 1)"):
            with pytest.raises(EvalError):
                eval_form(loads(source), {}, TRIGGER_BUILTINS)

    def test_fuel_exhausted(self):
        with pytest.raises(FuelExhaustedError, match="fuel exhausted"):
            eval_form(loads("(and true true true)"), {}, TRIGGER_BUILTINS, fuel=2)

    def test_depth_exceeded(self):
        nested = "1"
        for _ in range(20):
            nested = f"(if true {nested})"
        with pytest.raises(EvalDepthError, match="depth exceeds"):
            eval_form(loads(nested), {}, {}, max_depth=5)


class TestFormVsVector:
    def test_form_is_not_vector(self):
        form = loads("(a b)")
        vector = loads("[a b]")
        assert isinstance(form, Form)
        assert not isinstance(vector, Form)
        assert isinstance(vector, list)

    def test_vector_data_not_called(self):
        # a vector is data: its head position never triggers a call
        assert eval_form(loads("[1 2]"), {}, TRIGGER_BUILTINS) == [1, 2]
        with pytest.raises(EvalError, match="undefined symbol"):
            eval_form(loads("[= 1 1]"), {}, TRIGGER_BUILTINS)


class TestValidateForm:
    def test_accepts_legit_expression(self):
        form = loads('(and (addressed-to-me? msg) (content-matches? msg "hi"))')
        validate_form(form, TRIGGER_BUILTINS, scope={"msg", "self"})

    def test_rejects_non_whitelisted_form(self):
        with pytest.raises(EvalError, match="unknown function or special form: spawn"):
            validate_form(loads('(spawn "x.tlon")'), TRIGGER_BUILTINS, scope={"msg", "self"})
        with pytest.raises(EvalError, match="unknown function or special form: while"):
            validate_form(loads("(while true)"), TRIGGER_BUILTINS, scope={"msg", "self"})

    def test_rejects_undefined_symbol(self):
        with pytest.raises(EvalError, match="undefined symbol: foo"):
            validate_form(loads("(addressed-to-me? foo)"), TRIGGER_BUILTINS, scope={"msg", "self"})

    def test_let_extends_scope(self):
        validate_form(loads("(let [x msg] (addressed-to-me? x))"), TRIGGER_BUILTINS, scope={"msg"})
        with pytest.raises(EvalError, match="undefined symbol: y"):
            validate_form(loads("(let [x 1] (addressed-to-me? y))"), TRIGGER_BUILTINS, scope={"msg"})


class TestBuiltins:
    def test_mentioned_builtin_removed(self):
        # mentioned? retired with the mail model: it fails as unknown function
        with pytest.raises(EvalError, match="unknown function: mentioned?"):
            eval_form(loads("(mentioned? msg)"), _env(_mail()), TRIGGER_BUILTINS)

    def test_addressed_to_me(self):
        # my address in To -> addressed to me
        assert eval_form(
            loads("(addressed-to-me? msg)"), _env(_mail(to=[_SELF_ADDRESS])), TRIGGER_BUILTINS
        )
        # To holds someone else -> not addressed
        assert not eval_form(
            loads("(addressed-to-me? msg)"), _env(_mail(to=["other@tlon.internal"])), TRIGGER_BUILTINS
        )
        # CC alone is not To
        assert not eval_form(
            loads("(addressed-to-me? msg)"),
            _env(_mail(to=["other@tlon.internal"], cc=[_SELF_ADDRESS])),
            TRIGGER_BUILTINS,
        )

    def test_cc_ed(self):
        assert eval_form(
            loads("(cc-ed? msg)"),
            _env(_mail(to=["other@tlon.internal"], cc=[_SELF_ADDRESS])),
            TRIGGER_BUILTINS,
        )
        assert not eval_form(
            loads("(cc-ed? msg)"), _env(_mail(to=[_SELF_ADDRESS])), TRIGGER_BUILTINS
        )

    def test_content_matches(self):
        assert eval_form(
            loads('(content-matches? msg "。$")'), _env(_mail(content="完。")), TRIGGER_BUILTINS
        )
        assert not eval_form(
            loads('(content-matches? msg "。$")'), _env(_mail(content="没完")), TRIGGER_BUILTINS
        )

    def test_content_matches_invalid_regex(self):
        with pytest.raises(EvalError, match="invalid regex"):
            eval_form(loads('(content-matches? msg "[")'), _env(_mail()), TRIGGER_BUILTINS)

    def test_contains(self):
        assert eval_form(loads('(contains? [1 2 3] 2)'), {}, TRIGGER_BUILTINS)
        assert not eval_form(loads("(contains? [1 2 3] 4)"), {}, TRIGGER_BUILTINS)
        assert eval_form(loads('(contains? "hello" "ell")'), {}, TRIGGER_BUILTINS)
        with pytest.raises(EvalError, match=r"contains\? failed"):
            eval_form(loads("(contains? [1 2] 1 2)"), {}, TRIGGER_BUILTINS)
        with pytest.raises(EvalError, match=r"contains\? expects"):
            eval_form(loads("(contains? 5 5)"), {}, TRIGGER_BUILTINS)

    def test_eq_type_distinct(self):
        assert eval_form(loads("(= 1 1 1)"), {}, TRIGGER_BUILTINS)
        assert not eval_form(loads("(= 1 2)"), {}, TRIGGER_BUILTINS)
        # keyword ≠ string, even with equal names
        assert not eval_form(loads('(= :a "a")'), {}, TRIGGER_BUILTINS)

    def test_not(self):
        assert eval_form(loads("(not false)"), {}, TRIGGER_BUILTINS) is True
        assert eval_form(loads("(not 1)"), {}, TRIGGER_BUILTINS) is False


class _FakeAgent:
    """Duck-typed Agent stand-in for TriggerEvaluator."""

    def __init__(self, trigger: TriggerSpec):
        self.id = _SELF["id"]
        self.name = _SELF["name"]
        self.address = _SELF_ADDRESS
        self.spec = SimpleNamespace(trigger=trigger)


class TestTriggerEvaluatorExpression:
    def _evaluator(self, source: str, **trigger_kwargs) -> TriggerEvaluator:
        return TriggerEvaluator(
            _FakeAgent(TriggerSpec(when=loads(source), **trigger_kwargs))
        )

    def test_expression_hit(self):
        evaluator = self._evaluator("(addressed-to-me? msg)")
        assert evaluator.should_respond(_mail(to=[_SELF_ADDRESS])) is True

    def test_expression_miss(self):
        evaluator = self._evaluator("(addressed-to-me? msg)")
        assert evaluator.should_respond(_mail(to=["other@tlon.internal"])) is False

    def test_expression_combined_conditions(self):
        evaluator = self._evaluator(
            '(and (addressed-to-me? msg) (content-matches? msg "？$"))'
        )
        assert evaluator.should_respond(_mail(content="你怎么看？", to=[_SELF_ADDRESS])) is True
        assert evaluator.should_respond(_mail(content="你好。", to=[_SELF_ADDRESS])) is False

    def test_eval_failure_safe_default(self):
        evaluator = self._evaluator('(content-matches? msg "[")')
        with patch("tlon.agents.trigger.logger") as mock_logger:
            assert evaluator.should_respond(_mail(to=[_SELF_ADDRESS])) is False
        warning_events = [c.args[0] for c in mock_logger.warning.call_args_list]
        assert "trigger_eval_failed" in warning_events

    def test_when_takes_precedence_over_data_fields(self):
        # data mode would fire (addressed), but the expression says no
        evaluator = self._evaluator("(= 1 2)", on_mention=True)
        assert evaluator.should_respond(_mail(to=[_SELF_ADDRESS])) is False

    def test_own_mail_skipped_in_expression_mode(self):
        evaluator = self._evaluator("(= 1 1)")
        assert evaluator.should_respond(_mail(from_address=_SELF_ADDRESS)) is False

    def test_data_mode_to_or_cc(self):
        agent = _FakeAgent(TriggerSpec(on_mention=True))
        evaluator = TriggerEvaluator(agent)
        assert evaluator.should_respond(_mail(to=[_SELF_ADDRESS])) is True
        assert evaluator.should_respond(_mail(cc=[_SELF_ADDRESS])) is True
        assert evaluator.should_respond(_mail(to=["other@tlon.internal"])) is False

        deaf = TriggerEvaluator(_FakeAgent(TriggerSpec(on_mention=False)))
        assert deaf.should_respond(_mail(to=[_SELF_ADDRESS])) is False


class TestSpecTriggerExpression:
    def test_expression_mode_accepted(self):
        spec = AgentSpec.from_edn(
            loads('{:version 1 :trigger (and (addressed-to-me? msg) (cc-ed? msg))}')
        )
        assert isinstance(spec.trigger.when, Form)

    def test_mentioned_builtin_removed(self):
        # mentioned? retired with the mail model (R1b): specs referencing
        # it fail validation at load time.
        with pytest.raises(SpecValidationError, match="mentioned?"):
            AgentSpec.from_edn(loads('{:version 1 :trigger (mentioned? msg)}'))

    def test_data_mode_when_is_none(self):
        spec = AgentSpec.from_edn(loads("{:version 1 :trigger {:on-mention true}}"))
        assert spec.trigger.when is None
        assert spec.trigger.on_mention is True

    def test_non_whitelisted_expression_rejected_at_load(self):
        with pytest.raises(SpecValidationError, match="invalid :trigger expression"):
            AgentSpec.from_edn(loads('{:version 1 :trigger (spawn "x.tlon")}'))

    def test_undefined_symbol_rejected_at_load(self):
        with pytest.raises(SpecValidationError, match="undefined symbol"):
            AgentSpec.from_edn(loads("{:version 1 :trigger (addressed-to-me? foo)}"))

    def test_dict_roundtrip_preserves_when(self):
        spec = AgentSpec.from_edn(loads("{:version 1 :trigger (addressed-to-me? msg)}"))
        assert AgentSpec.from_dict(spec.to_dict()).trigger.when == spec.trigger.when
