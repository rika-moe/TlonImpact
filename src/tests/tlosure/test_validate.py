"""Static validation tests (``tlosure.validate``).

Complements ``TestValidateForm`` in ``test_eval.py``: that class pins the
happy path plus the head/scope basics; this suite covers the recursive
validation of nested data literals, the special-form shape errors not
already asserted there, the while/recur tail-position rules (the reverse
direction — every non-tail position rejects ``recur``), and the scoping
semantics of each binding construct.
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

    def test_filter_shape_errors(self):
        with pytest.raises(EvalError, match="filter expects a binding, a body, and a collection"):
            validate_form(loads("(filter x (> x 1))"), _BUILTINS, scope=set())
        with pytest.raises(EvalError, match="filter binding must be a symbol"):
            validate_form(loads("(filter 1 (> 1 1) [1 2])"), _BUILTINS, scope=set())

    def test_reduce_element_binder_must_be_symbol(self):
        # test_eval.py covers a non-symbol accumulator; this is the other branch.
        with pytest.raises(EvalError, match="reduce bindings must be symbols"):
            validate_form(loads("(reduce acc 1 (+ acc 1) 0 [1])"), _BUILTINS, scope=set())


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

    def test_recur_in_map_filter_reduce_body_rejected(self):
        # Iteration bodies are not tail positions of the enclosing loop.
        for source in (
            "(while [i 0] (< i 3) (map x (recur (inc i)) [1]))",
            "(while [i 0] (< i 3) (filter x (recur (inc i)) [1]))",
            "(while [i 0] (< i 3) (reduce acc x (recur (inc i)) 0 [1]))",
        ):
            with pytest.raises(EvalError, match="recur must be in tail position of while"):
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

    def test_map_filter_binder_scoped_to_body_only(self):
        validate_form(loads("(map x (+ x 1) xs)"), _BUILTINS, scope={"xs"})
        validate_form(loads("(filter x (> x 1) xs)"), _BUILTINS, scope={"xs"})
        # The collection argument does not see the binder.
        with pytest.raises(EvalError, match="undefined symbol: x"):
            validate_form(loads("(map x (+ x 1) x)"), _BUILTINS, scope=set())

    def test_reduce_binders_scoped_to_body_only(self):
        validate_form(loads("(reduce acc x (+ acc x) 0 xs)"), _BUILTINS, scope={"xs"})
        # Neither init nor collection sees the binders.
        with pytest.raises(EvalError, match="undefined symbol: acc"):
            validate_form(loads("(reduce acc x (+ acc x) acc [1])"), _BUILTINS, scope=set())
        with pytest.raises(EvalError, match="undefined symbol: x"):
            validate_form(loads("(reduce acc x (+ acc x) 0 x)"), _BUILTINS, scope=set())
