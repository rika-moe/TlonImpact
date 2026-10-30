"""Static validation tests (``tlosure.validate``).

Complements ``TestValidateForm`` in ``test_eval.py``: that class pins the
happy path plus the head/scope basics; this suite covers the recursive
validation of nested data literals, the special-form shape errors not
already asserted there, the while/recur tail-position rules (the reverse
direction — every non-tail position rejects ``recur``), the scoping
semantics of each binding construct, and the ADR 0021 function forms
(``defn`` / ``fn``: scope threading, parameter shapes, the ``recur``
function boundary).
"""

import pytest

from tlosure.builtins import ARITHMETIC_BUILTINS, CORE_BUILTINS, Builtin, EvalError
from tlosure.edn import loads
from tlosure.eval import validate_form

_BUILTINS: dict[str, Builtin] = {**CORE_BUILTINS, **ARITHMETIC_BUILTINS}


class TestNestedLiterals:
    """Data literals inside an expression are validated recursively."""

    def test_vector_elements_are_validated(self):
        validate_form(loads("[1 x]"), _BUILTINS, scope={"x"})
        with pytest.raises(EvalError, match="undefined symbol: y"):
            validate_form(loads("[1 y]"), _BUILTINS, scope={"x"})

    def test_nested_vector_elements_are_validated(self):
        with pytest.raises(EvalError, match="undefined symbol: y"):
            validate_form(loads("[[1] [y]]"), _BUILTINS, scope=set())

    def test_map_values_are_validated(self):
        validate_form(loads("{:a x}"), _BUILTINS, scope={"x"})
        with pytest.raises(EvalError, match="undefined symbol: y"):
            validate_form(loads("{:a y}"), _BUILTINS, scope=set())

    def test_map_keys_stay_literal(self):
        # Keys are data, not expressions: an unscoped symbol key is fine.
        validate_form(loads("{anything 1}"), _BUILTINS, scope=set())

    def test_literals_inside_builtin_arguments_are_validated(self):
        validate_form(loads("(= [x] [1])"), _BUILTINS, scope={"x"})
        with pytest.raises(EvalError, match="undefined symbol: y"):
            validate_form(loads("(= {:k y} [1])"), _BUILTINS, scope={"x"})


class TestSpecialFormShapes:
    def test_empty_form_rejected(self):
        with pytest.raises(EvalError, match=r"empty form \(\) is not a valid expression"):
            validate_form(loads("()"), _BUILTINS, scope=set())

    def test_non_symbol_head_rejected(self):
        with pytest.raises(EvalError, match="form head must be a symbol"):
            validate_form(loads("(1 2)"), _BUILTINS, scope=set())

    def test_if_arity(self):
        with pytest.raises(EvalError, match="if expects 2 or 3 arguments, got 1"):
            validate_form(loads("(if true)"), _BUILTINS, scope=set())
        with pytest.raises(EvalError, match="if expects 2 or 3 arguments, got 4"):
            validate_form(loads("(if true 1 2 3)"), _BUILTINS, scope=set())

    def test_when_requires_a_test(self):
        with pytest.raises(EvalError, match="when expects a test form"):
            validate_form(loads("(when)"), _BUILTINS, scope=set())

    def test_let_requires_bindings_and_body(self):
        with pytest.raises(
            EvalError, match="let expects a binding vector and at least one body form"
        ):
            validate_form(loads("(let [x 1])"), _BUILTINS, scope=set())

    def test_let_binding_name_must_be_symbol(self):
        with pytest.raises(EvalError, match="let binding name must be a symbol"):
            validate_form(loads("(let [1 x] x)"), _BUILTINS, scope={"x"})
        with pytest.raises(EvalError, match="let binding name must be a symbol"):
            validate_form(loads("(let [:k 1] 1)"), _BUILTINS, scope=set())

    def test_filter_arguments_use_normal_validation(self) -> None:
        validate_form(loads("(filter (fn [x] (> x 1)))"), _BUILTINS, scope=set())
        with pytest.raises(EvalError, match="undefined symbol: x"):
            validate_form(loads("(filter x (> x 1) [1 2])"), _BUILTINS, scope=set())
        with pytest.raises(EvalError, match="fn parameter name must be a symbol"):
            validate_form(loads("(filter (fn [1] (> 1 1)) [1 2])"), _BUILTINS, scope=set())

    def test_reduce_callback_element_parameter_must_be_symbol(self) -> None:
        # Callback parameters are checked by the ordinary fn validator.
        with pytest.raises(EvalError, match="fn parameter name must be a symbol"):
            validate_form(loads("(reduce (fn [acc 1] (+ acc 1)) 0 [1])"), _BUILTINS, scope=set())


class TestTailPosition:
    """Every position that is not a ``while`` body tail rejects ``recur``."""

    def test_recur_in_while_test_rejected(self):
        # The test position is validated with the *outer* loop context
        # (non-tail), so with no outer while the error is "outside while".
        with pytest.raises(EvalError, match="recur outside while"):
            validate_form(loads("(while [i 0] (recur (inc i)) 1)"), _BUILTINS, scope=set())

    def test_recur_in_if_test_rejected(self):
        form = loads("(while [i 0] (< i 3) (if (recur (inc i)) 1 2))")
        with pytest.raises(EvalError, match="recur must be in tail position of while"):
            validate_form(form, _BUILTINS, scope=set())

    def test_recur_in_when_test_rejected(self):
        form = loads("(while [i 0] (< i 3) (when (recur (inc i)) 1))")
        with pytest.raises(EvalError, match="recur must be in tail position of while"):
            validate_form(form, _BUILTINS, scope=set())

    def test_recur_in_non_final_and_or_argument_rejected(self):
        for source in (
            "(while [i 0] (< i 3) (and (recur (inc i)) true))",
            "(while [i 0] (< i 3) (or (recur (inc i)) true))",
        ):
            with pytest.raises(EvalError, match="recur must be in tail position of while"):
                validate_form(loads(source), _BUILTINS, scope=set())

    def test_recur_in_let_init_rejected(self):
        form = loads("(while [i 0] (< i 3) (let [j (recur (inc i))] j))")
        with pytest.raises(EvalError, match="recur must be in tail position of while"):
            validate_form(form, _BUILTINS, scope=set())

    def test_recur_in_non_final_body_form_rejected(self):
        # Only the final form of a when/let body may carry the tail slot.
        for source in (
            "(while [i 0] (< i 3) (when true (recur (inc i)) 1))",
            "(while [i 0] (< i 3) (let [j i] (recur (inc j)) 1))",
        ):
            with pytest.raises(EvalError, match="recur must be in tail position of while"):
                validate_form(loads(source), _BUILTINS, scope=set())

    def test_recur_inside_data_literal_rejected(self):
        for source in (
            "(while [i 0] (< i 3) [(recur (inc i))])",
            "(while [i 0] (< i 3) {:k (recur (inc i))})",
        ):
            with pytest.raises(EvalError, match="recur must be in tail position of while"):
                validate_form(loads(source), _BUILTINS, scope=set())

    def test_recur_in_map_filter_reduce_body_rejected(self) -> None:
        # Callback functions cannot recur into their enclosing loop.
        for source in (
            "(while [i 0] (< i 3) (map (fn [x] (recur (inc i))) [1]))",
            "(while [i 0] (< i 3) (filter (fn [x] (recur (inc i))) [1]))",
            "(while [i 0] (< i 3) (reduce (fn [acc x] (recur (inc i))) 0 [1]))",
        ):
            with pytest.raises(EvalError, match="recur outside while"):
                validate_form(loads(source), _BUILTINS, scope=set())

    def test_recur_in_nested_while_binds_inner_loop(self):
        # The inner recur targets the inner while: its arity is the inner
        # loop's binding count, and it is legal in the inner body's tail.
        validate_form(
            loads("(while [i 0] (< i 3) (while [j 0] (< j 2) (recur (inc j))))"),
            _BUILTINS,
            scope=set(),
        )
        with pytest.raises(EvalError, match="recur expects 1 arguments, got 2"):
            validate_form(
                loads("(while [i 0] (< i 3) (while [j 0] (< j 2) (recur 1 2)))"),
                _BUILTINS,
                scope=set(),
            )

    def test_recur_arity_matches_binding_count(self):
        validate_form(
            loads("(while [i 0 j 10] (< i j) (recur (inc i) (dec j)))"),
            _BUILTINS,
            scope=set(),
        )

    def test_recur_in_if_else_branch_is_a_tail_position(self):
        # Both if branches inherit the tail slot (the test does not).
        validate_form(
            loads("(while [i 0] (< i 3) (if (> i 1) 0 (recur (inc i))))"),
            _BUILTINS,
            scope=set(),
        )


class TestScope:
    def test_caller_scope_set_is_not_mutated(self):
        scope: set[str] = set()
        validate_form(loads("(let [x 1] x)"), _BUILTINS, scope=scope)
        assert scope == set()

    def test_let_bindings_are_sequential(self):
        # A later init sees the earlier bindings, never the reverse.
        validate_form(loads("(let [x 1 y x] y)"), _BUILTINS, scope=set())
        with pytest.raises(EvalError, match="undefined symbol: y"):
            validate_form(loads("(let [x y y 1] y)"), _BUILTINS, scope=set())

    def test_let_binding_shadows_outer_scope(self):
        validate_form(loads("(let [x 2] x)"), _BUILTINS, scope={"x"})

    def test_let_bindings_do_not_leak(self):
        with pytest.raises(EvalError, match="undefined symbol: x"):
            validate_form(loads("(= (let [x 1] x) x)"), _BUILTINS, scope=set())

    def test_while_bindings_enter_test_and_body_scope(self):
        validate_form(loads("(while [i 0] (< i 3) (recur (inc i)))"), _BUILTINS, scope=set())
        with pytest.raises(EvalError, match="undefined symbol: j"):
            validate_form(loads("(while [i 0] (< j 3) (recur i))"), _BUILTINS, scope=set())

    def test_while_bindings_are_sequential(self):
        validate_form(loads("(while [i 0 j i] (< i 3) (recur (inc i) j))"), _BUILTINS, scope=set())

    def test_map_filter_callback_parameters_scoped_to_body_only(self) -> None:
        validate_form(loads("(map (fn [x] (+ x 1)) xs)"), _BUILTINS, scope={"xs"})
        validate_form(loads("(filter (fn [x] (> x 1)) xs)"), _BUILTINS, scope={"xs"})
        # The collection argument does not see the binder.
        with pytest.raises(EvalError, match="undefined symbol: x"):
            validate_form(loads("(map (fn [x] (+ x 1)) x)"), _BUILTINS, scope=set())

    def test_reduce_callback_parameters_scoped_to_body_only(self) -> None:
        validate_form(loads("(reduce (fn [acc x] (+ acc x)) 0 xs)"), _BUILTINS, scope={"xs"})
        # Neither init nor collection sees the binders.
        with pytest.raises(EvalError, match="undefined symbol: acc"):
            validate_form(loads("(reduce (fn [acc x] (+ acc x)) acc [1])"), _BUILTINS, scope=set())
        with pytest.raises(EvalError, match="undefined symbol: x"):
            validate_form(loads("(reduce (fn [acc x] (+ acc x)) 0 x)"), _BUILTINS, scope=set())


class TestFunctionValidation:
    """ADR 0021 validate mirror: scope threading, recur boundaries, shapes."""

    def test_fn_body_undefined_symbol_rejected(self):
        with pytest.raises(EvalError, match="undefined symbol: y"):
            validate_form(loads("(fn [x] (+ x y))"), _BUILTINS, scope=set())

    def test_fn_params_and_rest_enter_body_scope(self):
        validate_form(loads("(fn [x & xs] (contains? xs x))"), _BUILTINS, scope=set())

    def test_defn_use_before_define_rejected(self):
        with pytest.raises(EvalError, match="unknown function or special form: f"):
            validate_form(loads("(when true (f 1) (defn f [x] x))"), _BUILTINS, scope=set())

    def test_defn_define_before_use_passes(self):
        validate_form(loads("(when true (defn f [x] (inc x)) (f 1))"), _BUILTINS, scope=set())

    def test_defn_forward_reference_rejected(self):
        # Even though by-reference env capture would resolve g at
        # evaluation time, validation enforces define-before-use.
        with pytest.raises(EvalError, match="unknown function or special form: g"):
            validate_form(
                loads("(when true (defn f [x] (g x)) (defn g [x] x))"),
                _BUILTINS,
                scope=set(),
            )

    def test_defn_recursion_validates(self):
        validate_form(
            loads("(defn fact [n] (if (< n 2) 1 (* n (fact (dec n)))))"),
            _BUILTINS,
            scope=set(),
        )

    def test_recur_in_fn_body_rejected(self):
        with pytest.raises(EvalError, match="recur outside while"):
            validate_form(loads("(fn [x] (recur x))"), _BUILTINS, scope=set())

    def test_recur_in_fn_inside_while_rejected(self):
        # The enclosing while does not reach into the function body.
        with pytest.raises(EvalError, match="recur outside while"):
            validate_form(
                loads("(while [i 0] (< i 3) ((fn [] (recur (inc i)))))"),
                _BUILTINS,
                scope=set(),
            )

    def test_parameter_vector_shape_errors(self):
        with pytest.raises(EvalError, match="fn parameters: & must precede the final"):
            validate_form(loads("(fn [a & b c] a)"), _BUILTINS, scope=set())
        with pytest.raises(EvalError, match="fn parameters: & must precede the final"):
            validate_form(loads("(fn [&] 1)"), _BUILTINS, scope=set())
        with pytest.raises(EvalError, match="fn parameter name must be a symbol"):
            validate_form(loads("(fn [1] 1)"), _BUILTINS, scope=set())
        with pytest.raises(EvalError, match="defn parameters must be a vector"):
            validate_form(loads("(defn f (x) x)"), _BUILTINS, scope=set())

    def test_defn_shape_errors(self):
        with pytest.raises(EvalError, match="defn name must be a symbol"):
            validate_form(loads("(defn 1 [x] x)"), _BUILTINS, scope=set())
        with pytest.raises(EvalError, match="defn expects a name"):
            validate_form(loads("(defn f [x])"), _BUILTINS, scope=set())

    def test_form_head_is_validated(self):
        validate_form(loads("((fn [x] (inc x)) 5)"), _BUILTINS, scope=set())
        # The head expression itself is validated recursively.
        with pytest.raises(EvalError, match="undefined symbol: y"):
            validate_form(loads("((fn [x] (+ x y)) 5)"), _BUILTINS, scope=set())
        # Non-symbol, non-form heads stay shape errors.
        with pytest.raises(EvalError, match="form head must be a symbol"):
            validate_form(loads("(1 2)"), _BUILTINS, scope=set())

    def test_defn_does_not_leak_out_of_its_body(self):
        with pytest.raises(EvalError, match="undefined symbol: f"):
            validate_form(loads("(= (when true (defn f [x] x) (f 1)) f)"), _BUILTINS, scope=set())

    def test_defn_threads_through_while_body(self):
        """Mirror check: the evaluator shares one env dict across a while
        body, so a defn is visible to later sibling forms — the validator
        must admit exactly what the evaluator runs."""
        validate_form(
            loads("(while [i 0] (< i 2) (defn f [x] (+ x 1)) (f i) (recur (inc i)))"),
            _BUILTINS,
            scope=set(),
        )
        # Use before def in the same while body is still rejected.
        with pytest.raises(EvalError, match="unknown function or special form: f"):
            validate_form(
                loads("(while [i 0] (< i 2) (f i) (defn f [x] (+ x 1)) (recur (inc i)))"),
                _BUILTINS,
                scope=set(),
            )
