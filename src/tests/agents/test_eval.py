"""Tests for the restricted evaluator (tlon.agents.eval) and trigger expressions."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tlon.agents.edn import Form, Keyword, loads
from tlon.agents.eval import (
    LOOP_BUILTINS,
    SCRIPT_ACTION_NAMES,
    SCRIPT_BUILTINS,
    STAGE_COND_BUILTINS,
    TRIGGER_BUILTINS,
    ActionRequest,
    Builtin,
    EvalDepthError,
    EvalError,
    FuelExhaustedError,
    eval_form,
    make_script_builtins,
    validate_form,
)
from tlon.agents.spec import (
    AgentSpec,
    SpecValidationError,
    TriggerSpec,
    standard_behavior_edn,
)
from tlon.agents.trigger import AgentIdentity, TriggerEvaluator
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


def _evaluator_for(trigger: TriggerSpec) -> TriggerEvaluator:
    """Build a TriggerEvaluator from plain data (P4: no Agent needed)."""
    identity = AgentIdentity(id=_SELF["id"], name=_SELF["name"], address=_SELF_ADDRESS)
    return TriggerEvaluator(identity, SimpleNamespace(trigger=trigger))


class TestTriggerEvaluatorExpression:
    def _evaluator(self, source: str, **trigger_kwargs) -> TriggerEvaluator:
        trigger_kwargs.setdefault("on_mention", True)
        return _evaluator_for(TriggerSpec(when=loads(source), **trigger_kwargs))

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
        evaluator = _evaluator_for(TriggerSpec(on_mention=True))
        assert evaluator.should_respond(_mail(to=[_SELF_ADDRESS])) is True
        assert evaluator.should_respond(_mail(cc=[_SELF_ADDRESS])) is True
        assert evaluator.should_respond(_mail(to=["other@tlon.internal"])) is False

        deaf = _evaluator_for(TriggerSpec(on_mention=False))
        assert deaf.should_respond(_mail(to=[_SELF_ADDRESS])) is False


class TestSpecTriggerExpression:
    def _spec_with_trigger(self, trigger_source: str) -> AgentSpec:
        """Build a complete spec whose :trigger section is ``trigger_source``."""
        data = loads(standard_behavior_edn())
        data[Keyword("trigger")] = loads(trigger_source)
        return AgentSpec.from_edn(data)

    def test_expression_mode_accepted(self):
        spec = self._spec_with_trigger("(and (addressed-to-me? msg) (cc-ed? msg))")
        assert isinstance(spec.trigger.when, Form)

    def test_mentioned_builtin_removed(self):
        # mentioned? retired with the mail model (R1b): specs referencing
        # it fail validation at load time.
        with pytest.raises(SpecValidationError, match="mentioned?"):
            self._spec_with_trigger("(mentioned? msg)")

    def test_data_mode_when_is_none(self):
        spec = self._spec_with_trigger("{:on-mention true}")
        assert spec.trigger.when is None
        assert spec.trigger.on_mention is True

    def test_non_whitelisted_expression_rejected_at_load(self):
        with pytest.raises(SpecValidationError, match="invalid :trigger expression"):
            self._spec_with_trigger('(spawn "x.tlon")')

    def test_undefined_symbol_rejected_at_load(self):
        with pytest.raises(SpecValidationError, match="undefined symbol"):
            self._spec_with_trigger("(addressed-to-me? foo)")

    def test_dict_roundtrip_preserves_when(self):
        spec = self._spec_with_trigger("(addressed-to-me? msg)")
        assert AgentSpec.from_dict(spec.to_dict()).trigger.when == spec.trigger.when


# ── Loop until builtins (phase 7a) ──


def _loop_env(
    iteration: int = 0,
    error_count: int = 0,
    max_iterations: int = 10,
    max_consecutive_errors: int = 3,
) -> dict:
    return {
        "self": _SELF,
        "state": {"iteration": iteration, "error_count": error_count},
        "limits": {
            "max_iterations": max_iterations,
            "max_consecutive_errors": max_consecutive_errors,
        },
    }


class TestLoopBuiltins:
    def test_iteration_limit_reached(self):
        form = loads("(iteration-limit-reached?)")
        assert eval_form(form, _loop_env(iteration=10), LOOP_BUILTINS) is True
        assert eval_form(form, _loop_env(iteration=9), LOOP_BUILTINS) is False

    def test_error_limit_reached(self):
        form = loads("(error-limit-reached?)")
        assert eval_form(form, _loop_env(error_count=3), LOOP_BUILTINS) is True
        assert eval_form(form, _loop_env(error_count=2), LOOP_BUILTINS) is False

    def test_default_until_expression(self):
        until = loads("(or (iteration-limit-reached?) (error-limit-reached?))")
        assert eval_form(until, _loop_env(), LOOP_BUILTINS) is False
        assert eval_form(until, _loop_env(iteration=10), LOOP_BUILTINS) is True
        assert eval_form(until, _loop_env(error_count=3), LOOP_BUILTINS) is True

    def test_not_and_eq_available(self):
        env = _loop_env(iteration=10)
        assert eval_form(loads("(not (iteration-limit-reached?))"), env, LOOP_BUILTINS) is False
        assert eval_form(loads("(= 1 1)"), env, LOOP_BUILTINS) is True

    def test_missing_env_keys_safe_default_false(self):
        form = loads("(iteration-limit-reached?)")
        assert eval_form(form, {}, LOOP_BUILTINS) is False
        assert eval_form(form, {"state": {}}, LOOP_BUILTINS) is False
        assert eval_form(loads("(error-limit-reached?)"), {"limits": None}, LOOP_BUILTINS) is False

    def test_non_int_env_values_safe_default_false(self):
        env = {"state": {"iteration": "10"}, "limits": {"max_iterations": True}}
        assert eval_form(loads("(iteration-limit-reached?)"), env, LOOP_BUILTINS) is False

    def test_all_loop_builtins_pure(self):
        assert all(builtin.pure for builtin in LOOP_BUILTINS.values())

    def test_validate_accepts_default_until(self):
        validate_form(
            loads("(or (iteration-limit-reached?) (error-limit-reached?))"),
            LOOP_BUILTINS,
            scope=set(),
        )

    def test_validate_rejects_unknown_function_and_symbols(self):
        with pytest.raises(EvalError, match="unknown function"):
            validate_form(loads("(while true)"), LOOP_BUILTINS, scope=set())
        with pytest.raises(EvalError, match="undefined symbol"):
            validate_form(loads("(iteration-limit-reached? foo)"), LOOP_BUILTINS, scope=set())


# ── Stage condition builtins (phase 7c) ──


def _stage_cond_env(iteration: int = 0, error_count: int = 0, scratch: dict | None = None) -> dict:
    return {
        "state": {"iteration": iteration, "error_count": error_count},
        "scratch": scratch if scratch is not None else {},
    }


class TestStageCondBuiltins:
    def test_critique_failed(self):
        form = loads("(critique-failed?)")
        assert eval_form(form, _stage_cond_env(), STAGE_COND_BUILTINS) is False
        failed = {"critique": {"passed": False, "feedback": "fix"}}
        passed = {"critique": {"passed": True, "feedback": ""}}
        assert eval_form(form, _stage_cond_env(scratch=failed), STAGE_COND_BUILTINS) is True
        assert eval_form(form, _stage_cond_env(scratch=passed), STAGE_COND_BUILTINS) is False

    def test_critique_passed(self):
        form = loads("(critique-passed?)")
        assert eval_form(form, _stage_cond_env(), STAGE_COND_BUILTINS) is False
        failed = {"critique": {"passed": False, "feedback": "fix"}}
        passed = {"critique": {"passed": True, "feedback": ""}}
        assert eval_form(form, _stage_cond_env(scratch=passed), STAGE_COND_BUILTINS) is True
        assert eval_form(form, _stage_cond_env(scratch=failed), STAGE_COND_BUILTINS) is False

    def test_plan_exists(self):
        form = loads("(plan-exists?)")
        assert eval_form(form, _stage_cond_env(), STAGE_COND_BUILTINS) is False
        assert (
            eval_form(form, _stage_cond_env(scratch={"plan": ""}), STAGE_COND_BUILTINS) is False
        )
        assert (
            eval_form(form, _stage_cond_env(scratch={"plan": "1. step"}), STAGE_COND_BUILTINS)
            is True
        )
        # non-string values never count as a plan
        assert (
            eval_form(form, _stage_cond_env(scratch={"plan": ["step"]}), STAGE_COND_BUILTINS)
            is False
        )

    def test_first_iteration(self):
        form = loads("(first-iteration?)")
        assert eval_form(form, _stage_cond_env(iteration=0), STAGE_COND_BUILTINS) is True
        assert eval_form(form, _stage_cond_env(iteration=1), STAGE_COND_BUILTINS) is False

    def test_missing_env_keys_safe_default_false(self):
        for source in (
            "(critique-failed?)",
            "(critique-passed?)",
            "(plan-exists?)",
            "(first-iteration?)",
        ):
            assert eval_form(loads(source), {}, STAGE_COND_BUILTINS) is False
            assert eval_form(loads(source), {"scratch": None}, STAGE_COND_BUILTINS) is False

    def test_malformed_scratch_values_safe_default_false(self):
        env = _stage_cond_env(scratch={"critique": "broken", "plan": 42})
        assert eval_form(loads("(critique-failed?)"), env, STAGE_COND_BUILTINS) is False
        assert eval_form(loads("(critique-passed?)"), env, STAGE_COND_BUILTINS) is False
        assert eval_form(loads("(plan-exists?)"), env, STAGE_COND_BUILTINS) is False

    def test_not_and_eq_available(self):
        env = _stage_cond_env(scratch={"plan": "1. step"})
        assert eval_form(loads("(not (plan-exists?))"), env, STAGE_COND_BUILTINS) is False
        assert eval_form(loads("(= 1 1)"), env, STAGE_COND_BUILTINS) is True

    def test_all_stage_cond_builtins_pure(self):
        assert all(builtin.pure for builtin in STAGE_COND_BUILTINS.values())

    def test_validate_rejects_unknown_function_and_symbols(self):
        with pytest.raises(EvalError, match="unknown function"):
            validate_form(loads("(iteration-limit-reached?)"), STAGE_COND_BUILTINS, scope=set())
        with pytest.raises(EvalError, match="undefined symbol"):
            validate_form(loads("(plan-exists? foo)"), STAGE_COND_BUILTINS, scope=set())


# ── Script member builtins (phase P5a) ──


class _FakeScriptHost:
    """ScriptHostQueries test double: fixed candidates, argument-echoing
    predicates."""

    def __init__(self, sleeping: set[str] | None = None, candidates: list[str] | None = None):
        self._sleeping = sleeping or set()
        self._candidates = candidates or []

    def is_sleeping(self, agent_id):
        return agent_id in self._sleeping

    def is_running(self, agent_id):
        return isinstance(agent_id, str) and agent_id not in self._sleeping

    def is_lazy(self, agent_id):
        return False

    def start_all_candidates(self) -> list[str]:
        return list(self._candidates)


class TestMapValueEvaluation:
    """P5a: maps are data — keys stay literal, values evaluate."""

    def test_map_values_evaluate(self):
        result = eval_form(
            loads('{:agent (get event :recipient) :reason "wake"}'),
            {"event": {"recipient": "a-001"}},
            SCRIPT_BUILTINS,
        )
        assert result == {Keyword("agent"): "a-001", Keyword("reason"): "wake"}

    def test_map_values_validate(self):
        with pytest.raises(EvalError, match="unknown function"):
            validate_form(loads("{:k (no-such-fn)}"), SCRIPT_BUILTINS, scope=set())
        with pytest.raises(EvalError, match="undefined symbol"):
            validate_form(loads("{:k foo}"), SCRIPT_BUILTINS, scope=set())


class TestScriptGetAccessor:
    def test_get_reads_dict_payload(self):
        env = {"event": {"recipient": "a-001", "mail": "ignored"}}
        assert eval_form(loads("(get event :recipient)"), env, SCRIPT_BUILTINS) == "a-001"

    def test_get_missing_key_yields_none(self):
        assert eval_form(loads("(get event :missing)"), {"event": {}}, SCRIPT_BUILTINS) is None

    def test_get_default_argument(self):
        assert (
            eval_form(loads('(get event :missing "fallback")'), {"event": {}}, SCRIPT_BUILTINS)
            == "fallback"
        )

    def test_get_non_dict_target_yields_default(self):
        """Malformed payloads never raise — script rules see arbitrary events."""
        assert eval_form(loads("(get event :recipient)"), {"event": "garbage"}, SCRIPT_BUILTINS) is None
        assert eval_form(loads("(get event :recipient)"), {"event": None}, SCRIPT_BUILTINS) is None


class TestScriptHostPredicates:
    def test_host_predicates_not_in_module_registry(self):
        assert "sleeping?" not in SCRIPT_BUILTINS
        assert "running?" not in SCRIPT_BUILTINS
        assert "lazy?" not in SCRIPT_BUILTINS
        hostless = make_script_builtins(None)
        assert "sleeping?" not in hostless
        with pytest.raises(EvalError, match="unknown function"):
            validate_form(loads("(sleeping? event)"), hostless, scope={"event"})

    def test_host_predicates_closure_injected(self):
        host = _FakeScriptHost(sleeping={"a-001"})
        builtins = make_script_builtins(host)
        env = {"event": {"recipient": "a-001"}}
        assert eval_form(loads("(sleeping? (get event :recipient))"), env, builtins) is True
        assert eval_form(loads("(running? (get event :recipient))"), env, builtins) is False
        assert eval_form(loads("(lazy? (get event :recipient))"), env, builtins) is False

    def test_host_predicates_are_pure(self):
        builtins = make_script_builtins(_FakeScriptHost())
        assert builtins["sleeping?"].pure
        assert builtins["running?"].pure
        assert builtins["lazy?"].pure


class TestScriptActions:
    def test_actions_are_not_pure(self):
        builtins = make_script_builtins(_FakeScriptHost())
        assert not builtins["start"].pure
        assert not builtins["stop"].pure
        assert not builtins["start-all"].pure

    def test_start_returns_action_request_with_evaluated_args(self):
        builtins = make_script_builtins(_FakeScriptHost())
        env = {"event": {"recipient": "a-001"}}
        result = eval_form(
            loads('(start {:agent (get event :recipient) :reason "wake"})'), env, builtins
        )
        assert result == ActionRequest(
            kind="start", data={"agent_id": "a-001", "reason": "wake"}
        )

    def test_stop_returns_action_request(self):
        builtins = make_script_builtins(_FakeScriptHost())
        result = eval_form(
            loads('(stop {:agent "a-001" :reason "drain"})'), {"event": {}}, builtins
        )
        assert result == ActionRequest(
            kind="stop", data={"agent_id": "a-001", "reason": "drain"}
        )

    @pytest.mark.parametrize(
        "source",
        [
            '(start {:reason "wake"})',  # missing :agent
            '(start {:agent "" :reason "wake"})',  # empty :agent
            '(start {:agent nil :reason "wake"})',  # nil :agent
            '(start {:agent :kw :reason "wake"})',  # keyword :agent
            '(stop {:agent "a-001"})',  # missing :reason
            '(stop {:agent "a-001" :reason ""})',  # empty :reason
            '(start {:agent "a" :reason "r" :extra 1})',  # unknown option key
            "(start)",  # no options at all
            '(start-all {:reason "boot" :agent "a"})',  # start-all takes no :agent
        ],
    )
    def test_action_option_errors(self, source: str):
        builtins = make_script_builtins(_FakeScriptHost())
        with pytest.raises(EvalError):
            eval_form(loads(source), {"event": {}}, builtins)

    def test_start_all_expands_host_candidates(self):
        host = _FakeScriptHost(candidates=["a-001", "a-002"])
        builtins = make_script_builtins(host)
        result = eval_form(
            loads('(start-all {:reason "boot"})'), {"event": None}, builtins
        )
        assert result == [
            ActionRequest(kind="start", data={"agent_id": "a-001", "reason": "boot"}),
            ActionRequest(kind="start", data={"agent_id": "a-002", "reason": "boot"}),
        ]

    def test_start_all_without_host_raises(self):
        builtins = make_script_builtins(None)
        with pytest.raises(EvalError, match="start-all unavailable"):
            eval_form(loads('(start-all {:reason "boot"})'), {"event": None}, builtins)

    def test_action_evaluation_has_no_side_effects(self):
        """Evaluation is pure: requests are data, nothing is published."""
        host = _FakeScriptHost(candidates=["a-001"])
        builtins = make_script_builtins(host)
        env = {"event": {"recipient": "a-001"}}
        eval_form(loads('(start {:agent (get event :recipient) :reason "wake"})'), env, builtins)
        eval_form(loads('(start-all {:reason "boot"})'), env, builtins)
        assert host.start_all_candidates() == ["a-001"]  # read-only queries only


class TestScriptForwardAction:
    """P5b: the forward action (relay prototype's verb)."""

    def test_forward_is_a_script_action_and_not_pure(self):
        assert "forward" in SCRIPT_ACTION_NAMES
        builtins = make_script_builtins(_FakeScriptHost())
        assert not builtins["forward"].pure

    def test_forward_returns_action_request(self):
        builtins = make_script_builtins(_FakeScriptHost())
        result = eval_form(
            loads('(forward {:to ["gu-yanxi@tlon.internal"]})'), {"event": {}}, builtins
        )
        assert result == ActionRequest(
            kind="forward",
            data={"to": ["gu-yanxi@tlon.internal"], "cc": [], "subject": None},
        )

    def test_forward_full_options_and_evaluated_args(self):
        builtins = make_script_builtins(_FakeScriptHost())
        env = {"event": {"boss": "gu-yanxi@tlon.internal"}}
        result = eval_form(
            loads('(forward {:to [(get event :boss)] :cc ["a@tlon.internal"] '
                  ':subject "周报"})'),
            env,
            builtins,
        )
        assert result == ActionRequest(
            kind="forward",
            data={
                "to": ["gu-yanxi@tlon.internal"],
                "cc": ["a@tlon.internal"],
                "subject": "周报",
            },
        )

    @pytest.mark.parametrize(
        "source",
        [
            "(forward)",  # no options at all
            '(forward {:to []})',  # empty :to
            '(forward {:cc ["a@tlon.internal"]})',  # missing :to
            '(forward {:to "a@tlon.internal"})',  # :to not a vector
            '(forward {:to [""]})',  # empty address
            '(forward {:to [:kw]})',  # non-string address
            '(forward {:to ["a@tlon.internal"] :cc "b@tlon.internal"})',  # :cc not a vector
            '(forward {:to ["a@tlon.internal"] :subject 42})',  # non-string subject
            '(forward {:to ["a@tlon.internal"] :reason "x"})',  # unknown option key
        ],
    )
    def test_forward_option_errors(self, source: str):
        builtins = make_script_builtins(_FakeScriptHost())
        with pytest.raises(EvalError):
            eval_form(loads(source), {"event": {}}, builtins)
