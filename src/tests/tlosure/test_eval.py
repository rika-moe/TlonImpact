"""Restricted evaluator — phase D bounded loops and data transformation.

The evaluator (``tlosure.eval``) is the DSL's semantics: a closed
special-form whitelist, fuel-metered evaluation (:class:`FuelExhaustedError`
guarantees termination), and static validation (:func:`validate_form`).
Phase D broke the "no loops" rule — ``while``/``recur`` (Clojure loop/recur
style) and ``map``/``filter``/``reduce`` iterate under the same fuel bound —
so this suite pins the new semantics and the flipped assertions: ``loop`` /
bare ``recur`` are still rejected, ``while`` is now a special form whose
binding vector is enforced, and ``recur`` is only legal in a ``while``
body's tail position. ADR 0021 added full function capability (``defn`` /
``fn`` / closures / variadic parameters / recursion / first-class
functions), pinned in ``TestFunctions`` below; the constraint levers stay
the same fuel budget and depth cap.

This suite covers the language core only (ADR 0018 修订): the registry
is the host-agnostic general vocabulary (``CORE_BUILTINS`` +
``ARITHMETIC_BUILTINS``). Domain vocabulary — mail predicates, the
action family, ``make_member_builtins``, host injection,
``validate_member_actions`` — is tested in
``src/tests/agents/test_vocabulary.py``.
"""

import pytest

from tlosure.builtins import ARITHMETIC_BUILTINS, CORE_BUILTINS, Builtin, Closure
from tlosure.edn import Form, Keyword, loads
from tlosure.eval import (
    DEFAULT_MAX_DEPTH,
    MAX_FUEL,
    EvalDepthError,
    EvalError,
    Evaluator,
    FuelExhaustedError,
    eval_form,
    validate_form,
)

#: The general vocabulary: pure functions + arithmetic, no host domain.
_BUILTINS = {**CORE_BUILTINS, **ARITHMETIC_BUILTINS}


def _recorded(recorded: list) -> dict:
    """A registry with a side-channel ``record`` builtin for loop observation."""
    builtins = dict(_BUILTINS)
    builtins["record"] = Builtin(lambda env, value: recorded.append(value) or value)
    return builtins


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
        assert eval_form(loads("[1 2 x]"), {"x": 3}, _BUILTINS) == [1, 2, 3]


class TestSpecialForms:
    def test_if_three_branches(self):
        assert eval_form(loads("(if true 1 2)"), {}, _BUILTINS) == 1
        assert eval_form(loads("(if false 1 2)"), {}, _BUILTINS) == 2

    def test_if_without_else_returns_nil(self):
        assert eval_form(loads("(if false 1)"), {}, _BUILTINS) is None

    def test_if_arity_error(self):
        with pytest.raises(EvalError, match="if expects 2 or 3 arguments"):
            eval_form(loads("(if true)"), {}, _BUILTINS)

    def test_when(self):
        assert eval_form(loads("(when false 1)"), {}, _BUILTINS) is None
        assert eval_form(loads("(when true 1 2)"), {}, _BUILTINS) == 2

    def test_let_sequential_bindings(self):
        # y sees the earlier binding of x
        assert eval_form(loads("(let [x 1 y x] y)"), {}, _BUILTINS) == 1
        # later binding shadows earlier
        assert eval_form(loads("(let [x 1 x 2] x)"), {}, _BUILTINS) == 2

    def test_let_requires_vector_bindings(self):
        with pytest.raises(EvalError, match="let bindings must be a vector"):
            eval_form(loads("(let (x 1) x)"), {}, _BUILTINS)
        with pytest.raises(EvalError, match="even number of forms"):
            eval_form(loads("(let [x] x)"), {}, _BUILTINS)

    def test_and_or_return_values(self):
        assert eval_form(loads("(and 1 2)"), {}, _BUILTINS) == 2
        assert eval_form(loads("(and 1 nil 3)"), {}, _BUILTINS) is None
        assert eval_form(loads("(or nil 5)"), {}, _BUILTINS) == 5
        assert eval_form(loads("(or nil false)"), {}, _BUILTINS) is False

    def test_and_or_short_circuit(self):
        calls: list[int] = []

        def mark(env: dict, value: int) -> int:
            calls.append(value)
            return value

        builtins = {**_BUILTINS, "mark": Builtin(mark)}
        assert eval_form(loads("(and false (mark 1))"), {}, builtins) is False
        assert eval_form(loads("(or true (mark 1))"), {}, builtins) is True
        assert calls == []


class TestArithmetic:
    """Phase D: bounded loops and data transformation need numbers."""

    def test_add_sub_mul(self):
        assert eval_form(loads("(+ 1 2 3)"), {}, _BUILTINS) == 6
        assert eval_form(loads("(- 5 2)"), {}, _BUILTINS) == 3
        assert eval_form(loads("(* 2 3 4)"), {}, _BUILTINS) == 24

    def test_inc_dec(self):
        assert eval_form(loads("(inc 4)"), {}, _BUILTINS) == 5
        assert eval_form(loads("(dec 4)"), {}, _BUILTINS) == 3

    def test_comparisons(self):
        assert eval_form(loads("(< 1 2)"), {}, _BUILTINS) is True
        assert eval_form(loads("(> 2 1)"), {}, _BUILTINS) is True
        assert eval_form(loads("(<= 1 1)"), {}, _BUILTINS) is True
        assert eval_form(loads("(>= 1 2)"), {}, _BUILTINS) is False

    def test_non_number_rejected(self):
        with pytest.raises(EvalError, match="expected a number"):
            eval_form(loads('(+ 1 "x")'), {}, _BUILTINS)


class TestStr:
    """Issue : variadic string concatenation for spec-side text assembly."""

    def test_strings_concatenate_verbatim(self):
        assert eval_form(loads('(str "a" "b" "c")'), {}, _BUILTINS) == "abc"

    def test_non_strings_use_edn_write_form(self):
        assert (
            eval_form(loads('(str "id: " :foo ", n=" 3 ", " [1 2])'), {}, _BUILTINS)
            == "id: :foo, n=3, [1 2]"
        )

    def test_nil_and_bool(self):
        assert eval_form(loads('(str nil " " true)'), {}, _BUILTINS) == "nil true"

    def test_no_args_is_empty_string(self):
        assert eval_form(loads("(str)"), {}, _BUILTINS) == ""


class TestWhileRecur:
    """Phase D: Clojure-style bounded loops, state through the binding vector."""

    def test_recur_rebinds_and_accumulates(self):
        recorded: list = []
        result = eval_form(
            loads("(while [i 0] (< i 3) (record i) (recur (inc i)))"),
            {},
            _recorded(recorded),
        )
        assert recorded == [0, 1, 2]
        assert result is None  # test false terminates the loop

    def test_no_recur_runs_once(self):
        recorded: list = []
        result = eval_form(loads("(while [i 0] (< i 5) (record i))"), {}, _recorded(recorded))
        assert recorded == [0]
        assert result == 0  # the body's last value

    def test_false_test_returns_none(self):
        recorded: list = []
        result = eval_form(loads("(while [i 0] (> i 5) (record i))"), {}, _recorded(recorded))
        assert recorded == []
        assert result is None

    def test_recur_through_if_branch_loops(self):
        recorded: list = []
        eval_form(
            loads("(while [i 0] (< i 3) (record i) (if (< i 3) (recur (inc i))))"),
            {},
            _recorded(recorded),
        )
        assert recorded == [0, 1, 2]

    def test_outer_names_survive_rebinding(self):
        recorded: list = []
        result = eval_form(
            loads("(while [i 0] (< i 2) (record (+ base i)) (recur (inc i)))"),
            {"base": 10},
            _recorded(recorded),
        )
        assert recorded == [10, 11]
        assert result is None

    def test_fuel_exhausted_in_loop(self):
        with pytest.raises(FuelExhaustedError, match="fuel exhausted"):
            eval_form(
                loads("(while [i 0] (< i 1000) (recur (inc i)))"),
                {},
                _BUILTINS,
                fuel=30,
            )

    def test_recur_arity_mismatch_rejected(self):
        form = loads("(while [i 0] (< i 3) (recur (inc i) 1))")
        with pytest.raises(EvalError, match="recur expects 1 arguments, got 2"):
            validate_form(form, _BUILTINS, scope=set())
        with pytest.raises(EvalError, match="recur expects 1 arguments, got 2"):
            eval_form(form, {}, _BUILTINS)

    def test_recur_outside_while_rejected(self):
        with pytest.raises(EvalError, match="recur outside while"):
            eval_form(loads("(recur 1)"), {}, _BUILTINS)
        with pytest.raises(EvalError, match="recur outside while"):
            validate_form(loads("(recur 1)"), _BUILTINS, scope=set())

    def test_recur_in_builtin_argument_rejected(self):
        # A builtin argument is never a tail position — recur there could
        # not be the loop's tail, so it is rejected at validation.
        form = loads("(while [i 0] (< i 3) (+ (recur (inc i)) 1))")
        with pytest.raises(EvalError, match="recur must be in tail position of while"):
            validate_form(form, _BUILTINS, scope=set())

    def test_recur_tail_position_inherited(self):
        # if branches, trailing when/let bodies, and the final and/or
        # argument all inherit the while's tail slot (Clojure semantics).
        valid = [
            "(while [i 0] (< i 3) (if true (recur (inc i))))",
            "(while [i 0] (< i 3) (when true (recur (inc i))))",
            "(while [i 0] (< i 3) (let [j i] (recur (inc j))))",
            "(while [i 0] (< i 3) (and true (recur (inc i))))",
            "(while [i 0] (< i 3) (or false (recur (inc i))))",
        ]
        for source in valid:
            validate_form(loads(source), _BUILTINS, scope=set())

    def test_while_bindings_must_be_vector(self):
        with pytest.raises(EvalError, match="while bindings must be a vector"):
            validate_form(loads("(while true 1)"), _BUILTINS, scope=set())

    def test_while_needs_bindings_and_test(self):
        with pytest.raises(EvalError, match="while expects a binding vector and a test"):
            validate_form(loads("(while [i 0])"), _BUILTINS, scope=set())

    def test_while_bad_binding_name_rejected(self):
        with pytest.raises(EvalError, match="while binding name must be a symbol"):
            validate_form(loads("(while [1 0] (< 1 3) (recur 2))"), _BUILTINS, scope=set())


class TestMapFilterReduce:
    """Data transformation through first-class callback functions."""

    def test_map(self) -> None:
        assert eval_form(loads("(map (fn [x] (+ x 1)) [1 2 3])"), {}, _BUILTINS) == [2, 3, 4]
        assert eval_form(loads("(map (fn [x] (* x x)) [2 3])"), {}, _BUILTINS) == [4, 9]

    def test_filter(self) -> None:
        assert eval_form(loads("(filter (fn [x] (> x 2)) [1 2 3 4])"), {}, _BUILTINS) == [3, 4]

    def test_reduce(self) -> None:
        assert eval_form(loads("(reduce (fn [acc x] (+ acc x)) 0 [1 2 3])"), {}, _BUILTINS) == 6
        assert (
            eval_form(loads("(reduce (fn [acc x] (if (> x acc) x acc)) 0 [3 1 2])"), {}, _BUILTINS)
            == 3
        )

    def test_empty_collections(self) -> None:
        assert eval_form(loads("(map (fn [x] (+ x 1)) [])"), {}, _BUILTINS) == []
        assert eval_form(loads("(filter (fn [x] (> x 2)) [])"), {}, _BUILTINS) == []
        assert eval_form(loads("(reduce (fn [acc x] (+ acc x)) 0 [])"), {}, _BUILTINS) == 0

    def test_fuel_exhausted(self) -> None:
        with pytest.raises(FuelExhaustedError, match="fuel exhausted"):
            eval_form(
                loads("(map (fn [x] (+ x 1)) [1 2 3 4 5 6 7 8 9 10 11 12 13 14 15])"),
                {},
                _BUILTINS,
                fuel=10,
            )

    def test_map_wrong_arity_rejected(self) -> None:
        with pytest.raises(EvalError, match="map failed: .* missing"):
            eval_form(loads("(map (fn [x] (+ x 1)))"), {}, _BUILTINS)

    def test_map_callback_parameter_must_be_symbol(self) -> None:
        with pytest.raises(EvalError, match="fn parameter name must be a symbol"):
            validate_form(loads("(map (fn [1] (+ 1 1)) [1 2 3])"), _BUILTINS, scope=set())

    def test_map_body_unknown_symbol(self) -> None:
        with pytest.raises(EvalError, match="undefined symbol: y"):
            validate_form(loads("(map (fn [x] (+ y 1)) [1 2 3])"), _BUILTINS, scope=set())

    def test_reduce_wrong_arity_rejected(self) -> None:
        with pytest.raises(EvalError, match="reduce failed: .* missing"):
            eval_form(loads("(reduce (fn [acc x] (+ acc x)) 0)"), {}, _BUILTINS)

    def test_reduce_callback_parameter_must_be_symbol(self) -> None:
        with pytest.raises(EvalError, match="fn parameter name must be a symbol"):
            validate_form(loads("(reduce (fn [1 x] (+ 1 x)) 0 [1 2])"), _BUILTINS, scope=set())


class TestErrors:
    def test_unknown_function(self):
        with pytest.raises(EvalError, match="unknown function: frobnicate"):
            eval_form(loads("(frobnicate 1)"), {}, _BUILTINS)

    def test_empty_form(self):
        with pytest.raises(EvalError, match="empty form"):
            eval_form(loads("()"), {}, _BUILTINS)

    def test_non_symbol_head(self):
        with pytest.raises(EvalError, match="form head must be a symbol"):
            eval_form(loads("(1 2)"), {}, _BUILTINS)

    def test_fuel_exhausted(self):
        with pytest.raises(FuelExhaustedError, match="fuel exhausted"):
            eval_form(loads("(and true true true)"), {}, _BUILTINS, fuel=2)

    def test_depth_exceeded(self):
        nested = "1"
        for _ in range(20):
            nested = f"(if true {nested})"
        with pytest.raises(EvalDepthError, match="depth exceeds"):
            eval_form(loads(nested), {}, _BUILTINS, max_depth=5)


class TestBillingModel:
    """The precise fuel contract (ADR 0001): one step per evaluated
    node, untaken branches free, the hard cap enforced at the API
    boundary, exhaustion carrying the budget, one Evaluator = one
    shared counter."""

    def test_builtin_call_exact_steps(self):
        # (+ 1 2): form node + two literal args = 3 steps
        assert eval_form(loads("(+ 1 2)"), {}, _BUILTINS, fuel=3) == 3
        with pytest.raises(FuelExhaustedError):
            eval_form(loads("(+ 1 2)"), {}, _BUILTINS, fuel=2)

    def test_vector_exact_steps(self):
        # [1 2]: vector node + two elements = 3 steps
        assert eval_form(loads("[1 2]"), {}, _BUILTINS, fuel=3) == [1, 2]

    def test_symbol_exact_steps(self):
        assert eval_form(loads("x"), {"x": 7}, _BUILTINS, fuel=1) == 7

    def test_untaken_branch_is_free(self):
        # (if <test> <then> <else>): form + test + taken branch = 3 steps
        assert eval_form(loads("(if true 1 2)"), {}, _BUILTINS, fuel=3) == 1
        assert eval_form(loads("(if false 1 2)"), {}, _BUILTINS, fuel=3) == 2

    def test_and_shortcircuit_is_free(self):
        # (and nil ...): form + first arg = 2 steps, the rest never runs
        assert eval_form(loads("(and nil true true)"), {}, _BUILTINS, fuel=2) is None
        with pytest.raises(FuelExhaustedError):
            eval_form(loads("(and true true true)"), {}, _BUILTINS, fuel=2)

    def test_fuel_hard_cap(self):
        assert eval_form(loads("1"), {}, _BUILTINS, fuel=MAX_FUEL) == 1
        with pytest.raises(ValueError, match="fuel must lie"):
            eval_form(loads("1"), {}, _BUILTINS, fuel=MAX_FUEL + 1)
        with pytest.raises(ValueError, match="fuel must lie"):
            eval_form(loads("1"), {}, _BUILTINS, fuel=-1)

    def test_max_depth_floor(self):
        with pytest.raises(ValueError, match="max_depth"):
            eval_form(loads("1"), {}, _BUILTINS, max_depth=0)

    def test_exhaustion_carries_budget(self):
        with pytest.raises(FuelExhaustedError) as exc_info:
            eval_form(loads("(+ 1 2)"), {}, _BUILTINS, fuel=2)
        assert exc_info.value.budget == 2
        assert "budget 2" in str(exc_info.value)

    def test_shared_evaluator_draws_one_counter(self):
        evaluator = Evaluator(_BUILTINS, fuel=5, max_depth=DEFAULT_MAX_DEPTH)
        assert evaluator.eval(loads("(+ 1 2)"), {}, 0) == 3
        assert evaluator.fuel_remaining == 2
        with pytest.raises(FuelExhaustedError):
            evaluator.eval(loads("(+ 1 2)"), {}, 0)  # needs 3, only 2 left
        assert evaluator.budget == 5


class TestFormVsVector:
    def test_form_is_not_vector(self):
        form = loads("(a b)")
        vector = loads("[a b]")
        assert isinstance(form, Form)
        assert not isinstance(vector, Form)
        assert isinstance(vector, list)


class TestValidateForm:
    def test_accepts_legit_expression(self):
        form = loads('(and (contains? msg "hi") (not flag))')
        validate_form(form, _BUILTINS, scope={"msg", "flag"})

    def test_rejects_non_whitelisted_form(self):
        with pytest.raises(EvalError, match="unknown function or special form: frobnicate"):
            validate_form(loads("(frobnicate msg)"), _BUILTINS, scope={"msg", "self"})

    def test_rejects_undefined_symbol(self):
        with pytest.raises(EvalError, match="undefined symbol: foo"):
            validate_form(loads('(contains? foo "x")'), _BUILTINS, scope={"msg", "self"})

    def test_let_extends_scope(self):
        validate_form(loads('(let [x msg] (contains? x "hi"))'), _BUILTINS, scope={"msg"})
        with pytest.raises(EvalError, match="undefined symbol: y"):
            validate_form(loads('(let [x 1] (contains? y "hi"))'), _BUILTINS, scope={"msg"})


class TestFunctions:
    """ADR 0021: defn / fn / closures / variadic parameters / recursion /
    first-class functions — bounded by the same fuel and depth levers."""

    def test_defn_defines_and_calls(self):
        assert eval_form(loads("(when true (defn f [x] (inc x)) (f 41))"), {}, _BUILTINS) == 42

    def test_defn_returns_the_closure(self):
        result = eval_form(loads("(defn f [x] x)"), {}, _BUILTINS)
        assert isinstance(result, Closure)
        assert result.name == "f"

    def test_fn_immediate_application(self):
        assert eval_form(loads("((fn [x] (inc x)) 5)"), {}, _BUILTINS) == 6

    def test_closure_captures_let_binding(self):
        # The closure captures the definition-time environment.
        assert eval_form(loads("(let [base 10] ((fn [x] (+ base x)) 5))"), {}, _BUILTINS) == 15
        assert (
            eval_form(
                loads("(let [base 10] (defn add-base [x] (+ base x)) (add-base 5))"),
                {},
                _BUILTINS,
            )
            == 15
        )

    def test_variadic_rest_collects_extra_arguments(self):
        assert eval_form(
            loads("(when true (defn f [a & rest] rest) (f 1 2 3))"), {}, _BUILTINS
        ) == [2, 3]

    def test_variadic_rest_empty_vector(self):
        assert eval_form(loads("(when true (defn f [a & rest] rest) (f 1))"), {}, _BUILTINS) == []

    def test_variadic_only_rest(self):
        assert eval_form(loads("(when true (defn f [& xs] xs) (f 1 2))"), {}, _BUILTINS) == [1, 2]

    def test_recursion_factorial(self):
        assert (
            eval_form(
                loads("(when true (defn fact [n] (if (< n 2) 1 (* n (fact (dec n))))) (fact 5))"),
                {},
                _BUILTINS,
            )
            == 120
        )

    def test_recursion_fibonacci(self):
        assert (
            eval_form(
                loads(
                    "(when true (defn fib [n] (if (< n 2) n"
                    " (+ (fib (- n 1)) (fib (- n 2))))) (fib 8))"
                ),
                {},
                _BUILTINS,
            )
            == 21
        )

    def test_first_class_function_as_argument(self):
        assert (
            eval_form(
                loads("(when true (defn apply2 [f x] (f (f x))) (apply2 (fn [x] (* x 2)) 3))"),
                {},
                _BUILTINS,
            )
            == 12
        )

    def test_first_class_function_as_return_value(self):
        assert (
            eval_form(
                loads("(when true (defn make-adder [n] (fn [x] (+ x n))) ((make-adder 3) 4))"),
                {},
                _BUILTINS,
            )
            == 7
        )

    def test_defn_sibling_forms_see_the_definition(self):
        # defn binds into the current env dict, so later forms of the
        # same body resolve the name.
        assert (
            eval_form(
                loads("(when true (defn f [x] (inc x)) (defn g [x] (f (f x))) (g 0))"),
                {},
                _BUILTINS,
            )
            == 2
        )

    def test_arity_error_fixed_params(self):
        with pytest.raises(EvalError, match="f expects 2 arguments, got 1"):
            eval_form(loads("(when true (defn f [a b] a) (f 1))"), {}, _BUILTINS)

    def test_arity_error_variadic(self):
        with pytest.raises(EvalError, match="f expects at least 1 arguments, got 0"):
            eval_form(loads("(when true (defn f [a & r] a) (f))"), {}, _BUILTINS)

    def test_anonymous_arity_error_names_anonymous_fn(self):
        with pytest.raises(EvalError, match="anonymous fn expects 1 arguments, got 0"):
            eval_form(loads("((fn [x] x))"), {}, _BUILTINS)

    def test_infinite_recursion_hits_the_depth_cap(self):
        # Termination is guaranteed by the depth/fuel levers, not by the
        # absence of recursion (ADR 0021).
        with pytest.raises(EvalDepthError, match="depth exceeds"):
            eval_form(loads("(when true (defn f [x] (f (inc x))) (f 0))"), {}, _BUILTINS)

    def test_wide_recursion_hits_the_fuel_budget(self):
        with pytest.raises(FuelExhaustedError, match="fuel exhausted"):
            eval_form(
                loads("(when true (defn f [n] (if (< n 1) 0 (+ (f (- n 1)) (f (- n 1))))) (f 20))"),
                {},
                _BUILTINS,
            )

    def test_recur_does_not_cross_a_function_boundary(self):
        # Clojure semantics: applying a closure clears the enclosing loop
        # context, so recur inside a function body is "outside while".
        with pytest.raises(EvalError, match="recur outside while"):
            eval_form(
                loads(
                    "(when true (defn f [] (recur 1)) (while [i 0] (< i 1) (f) (recur (inc i))))"
                ),
                {},
                _BUILTINS,
            )

    def test_loop_stack_survives_a_function_call(self):
        # ...and the enclosing loop keeps working after the call returns.
        recorded: list = []
        eval_form(
            loads(
                "(when true (defn emit [x] (record x))"
                " (while [i 0] (< i 3) (emit i) (recur (inc i))))"
            ),
            {},
            _recorded(recorded),
        )
        assert recorded == [0, 1, 2]

    def test_env_value_is_not_callable(self):
        with pytest.raises(EvalError, match="msg is not callable"):
            eval_form(loads("(msg 1)"), {"msg": "hello"}, _BUILTINS)

    def test_form_head_must_evaluate_to_a_callable(self) -> None:
        with pytest.raises(EvalError, match="is not callable"):
            eval_form(loads("((+ 1 2) 3)"), {}, _BUILTINS)

    def test_str_on_a_closure_is_an_eval_error(self):
        # dumps() has no Closure case: the TypeError surfaces through the
        # builtin-call wrapper as a plain EvalError (spec bug).
        with pytest.raises(EvalError, match="str failed: cannot dump Closure as EDN"):
            eval_form(loads("(str (fn [x] x))"), {}, _BUILTINS)

    def test_defn_shape_errors(self):
        with pytest.raises(EvalError, match="defn name must be a symbol"):
            eval_form(loads("(defn 1 [x] x)"), {}, _BUILTINS)
        with pytest.raises(EvalError, match="defn parameters must be a vector"):
            eval_form(loads("(defn f (x) x)"), {}, _BUILTINS)
        with pytest.raises(
            EvalError,
            match="defn expects a name, a parameter vector, and at least one body form",
        ):
            eval_form(loads("(defn f [x])"), {}, _BUILTINS)

    def test_fn_shape_errors(self):
        with pytest.raises(
            EvalError, match="fn expects a parameter vector and at least one body form"
        ):
            eval_form(loads("(fn [x])"), {}, _BUILTINS)
        with pytest.raises(EvalError, match="fn parameters: & must precede the final"):
            eval_form(loads("(fn [a & b c] a)"), {}, _BUILTINS)


class TestFlippedAssertions:
    """Phase D: loops are legal now — but still bounded and guarded.

    The pre-D suite asserted ``(loop [x 1] x)`` / ``(recur 1)`` /
    ``(while true)`` were rejected wholesale. ``while`` is now a special
    form and ``recur`` works inside one; what remains rejected is the
    unloopable core (``loop``, bare ``recur``) and a malformed ``while``.
    """

    def test_loop_is_still_unknown(self):
        with pytest.raises(EvalError, match="unknown function or special form: loop"):
            validate_form(loads("(loop [x 1] x)"), _BUILTINS, scope=set())
        with pytest.raises(EvalError, match="unknown function: loop"):
            eval_form(loads("(loop [x 1] x)"), {}, _BUILTINS)

    def test_def_lambda_still_unknown(self):
        # ADR 0021 added defn/fn as special forms, but ``def`` and
        # ``lambda`` remain outside the language.
        for source in ("(def x 1)", "(lambda (x) x)"):
            with pytest.raises(EvalError, match="unknown"):
                eval_form(loads(source), {}, _BUILTINS)

    def test_defn_fn_are_special_forms_now(self):
        # The flipped side of ADR 0021: defn/fn evaluate to closures.
        assert eval_form(loads("(when true (defn f [x] (inc x)) (f 1))"), {}, _BUILTINS) == 2
        assert eval_form(loads("((fn [x] (inc x)) 1)"), {}, _BUILTINS) == 2

    def test_recur_alone_still_rejected(self):
        with pytest.raises(EvalError, match="recur outside while"):
            eval_form(loads("(recur 1)"), {}, _BUILTINS)

    def test_while_true_now_reports_bindings(self):
        # Previously "unknown function or special form: while"; now while is
        # a special form and the shape error is about its binding vector.
        with pytest.raises(EvalError, match="while bindings must be a vector"):
            validate_form(loads("(while true 1)"), _BUILTINS, scope=set())


class TestQuote:
    @pytest.mark.parametrize(
        "source",
        ["(quote unbound)", "(quote (unregistered! x))", "(quote {:stages [(think)]})"],
    )
    def test_code_is_returned_as_data(self, source: str) -> None:
        form = loads(source)
        validate_form(form, _BUILTINS, scope=set())
        assert eval_form(form, {}, _BUILTINS) == form[1]

    @pytest.mark.parametrize("source", ["(quote)", "(quote 1 2)"])
    def test_exactly_one_argument(self, source: str) -> None:
        with pytest.raises(EvalError, match="quote expects 1 argument"):
            eval_form(loads(source), {}, _BUILTINS)
        with pytest.raises(EvalError, match="quote expects 1 argument"):
            validate_form(loads(source), _BUILTINS, scope=set())

    def test_quoted_effect_does_not_execute(self) -> None:
        recorded: list = []
        form = loads('(quote (record "not executed"))')
        assert eval_form(form, {}, _recorded(recorded)) == form[1]
        assert recorded == []
