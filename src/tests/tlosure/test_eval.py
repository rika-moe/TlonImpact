"""Restricted evaluator — phase D bounded loops and data transformation.

The evaluator (``tlosure.eval``) is the DSL's semantics: a closed
special-form whitelist, fuel-metered evaluation (:class:`FuelExhaustedError`
guarantees termination), and static validation (:func:`validate_form`).
Phase D broke the "no loops" rule — ``while``/``recur`` (Clojure loop/recur
style) and ``map``/``filter``/``reduce`` iterate under the same fuel bound —
so this suite pins the new semantics and the flipped assertions: ``loop`` /
bare ``recur`` are still rejected, ``while`` is now a special form whose
binding vector is enforced, and ``recur`` is only legal in a ``while``
body's tail position.
"""

import pytest

from tlosure.edn import Form, Keyword, loads
from tlosure.eval import (
    ActionRequest,
    Builtin,
    EvalDepthError,
    EvalError,
    FuelExhaustedError,
    eval_form,
    make_member_builtins,
    validate_form,
)

#: The unified member registry (pure predicates + arithmetic + actions).
_BUILTINS = make_member_builtins()


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


class TestMemberRoster:
    """Issue : the host-injected social roster query (same injection
    pattern as ``sleeping?`` — absent from the host-less registry)."""

    class _Host:
        def is_sleeping(self, agent_id: object) -> bool:
            return False

        def is_running(self, agent_id: object) -> bool:
            return True

        def is_lazy(self, agent_id: object) -> bool:
            return False

        def start_all_candidates(self) -> list[str]:
            return []

        def member_roster(self) -> str:
            return "阿白（a-bai-1a2b3c4d）"

    def test_with_host_returns_roster(self):
        builtins = make_member_builtins(self._Host())
        assert eval_form(loads("(member-roster)"), {}, builtins) == "阿白（a-bai-1a2b3c4d）"

    def test_without_host_is_unregistered(self):
        assert "member-roster" not in _BUILTINS
        with pytest.raises(EvalError, match="unknown function: member-roster"):
            eval_form(loads("(member-roster)"), {}, _BUILTINS)


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
        result = eval_form(
            loads("(while [i 0] (< i 5) (record i))"), {}, _recorded(recorded)
        )
        assert recorded == [0]
        assert result == 0  # the body's last value

    def test_false_test_returns_none(self):
        recorded: list = []
        result = eval_form(
            loads("(while [i 0] (> i 5) (record i))"), {}, _recorded(recorded)
        )
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
            loads(
                "(while [i 0] (< i 2) (record (+ base i)) (recur (inc i)))"
            ),
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
    """Phase D: data transformation with explicitly bound element variables."""

    def test_map(self):
        assert eval_form(loads("(map x (+ x 1) [1 2 3])"), {}, _BUILTINS) == [2, 3, 4]
        assert eval_form(loads("(map x (* x x) [2 3])"), {}, _BUILTINS) == [4, 9]

    def test_filter(self):
        assert eval_form(loads("(filter x (> x 2) [1 2 3 4])"), {}, _BUILTINS) == [3, 4]

    def test_reduce(self):
        assert eval_form(loads("(reduce acc x (+ acc x) 0 [1 2 3])"), {}, _BUILTINS) == 6
        assert (
            eval_form(loads("(reduce acc x (if (> x acc) x acc) 0 [3 1 2])"), {}, _BUILTINS)
            == 3
        )

    def test_empty_collections(self):
        assert eval_form(loads("(map x (+ x 1) [])"), {}, _BUILTINS) == []
        assert eval_form(loads("(filter x (> x 2) [])"), {}, _BUILTINS) == []
        assert eval_form(loads("(reduce acc x (+ acc x) 0 [])"), {}, _BUILTINS) == 0

    def test_fuel_exhausted(self):
        with pytest.raises(FuelExhaustedError, match="fuel exhausted"):
            eval_form(loads("(map x (+ x 1) [1 2 3 4 5 6 7 8 9 10 11 12 13 14 15])"),
                      {}, _BUILTINS, fuel=10)

    def test_map_wrong_arity_rejected(self):
        with pytest.raises(EvalError, match="map expects a binding, a body, and a collection"):
            validate_form(loads("(map x (+ x 1))"), _BUILTINS, scope=set())

    def test_map_binder_must_be_symbol(self):
        with pytest.raises(EvalError, match="map binding must be a symbol"):
            validate_form(loads("(map 1 (+ 1 1) [1 2 3])"), _BUILTINS, scope=set())

    def test_map_body_unknown_symbol(self):
        with pytest.raises(EvalError, match="undefined symbol: y"):
            validate_form(loads("(map x (+ y 1) [1 2 3])"), _BUILTINS, scope=set())

    def test_reduce_wrong_arity_rejected(self):
        with pytest.raises(
            EvalError, match="reduce expects accumulator, element, body, init, and collection"
        ):
            validate_form(loads("(reduce acc x (+ acc x) 0)"), _BUILTINS, scope=set())

    def test_reduce_binder_must_be_symbol(self):
        with pytest.raises(EvalError, match="reduce bindings must be symbols"):
            validate_form(loads("(reduce 1 x (+ 1 x) 0 [1 2])"), _BUILTINS, scope=set())


class TestActionCollection:
    """Phase D invariant: loop body actions are just more recorded requests."""

    def test_while_body_collects_each_emit(self):
        collector: list[ActionRequest] = []
        builtins = make_member_builtins(collector=collector)
        result = eval_form(
            loads('(while [i 0] (< i 3) (emit "x") (recur (inc i)))'), {}, builtins
        )
        assert result is None
        assert len(collector) == 3
        assert all(request.kind == "emit" for request in collector)

    def test_map_returns_request_list(self):
        # Deterministic rules collect the action's return value: a map of
        # emits is a list of requests, all of which reach the engine.
        result = eval_form(loads('(map i (emit "x") [1 2 3])'), {}, make_member_builtins())
        assert len(result) == 3
        assert all(request.kind == "emit" for request in result)


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


class TestFormVsVector:
    def test_form_is_not_vector(self):
        form = loads("(a b)")
        vector = loads("[a b]")
        assert isinstance(form, Form)
        assert not isinstance(vector, Form)
        assert isinstance(vector, list)


class TestValidateForm:
    def test_accepts_legit_expression(self):
        form = loads('(and (addressed-to-me? msg) (content-matches? msg "hi"))')
        validate_form(form, _BUILTINS, scope={"msg", "self"})

    def test_rejects_non_whitelisted_form(self):
        # A genuinely unknown form is rejected; action names are valid
        # (impure) expressions here — purity is enforced at the member
        # action layer (validate_member_action), not in validate_form.
        with pytest.raises(EvalError, match="unknown function or special form: frobnicate"):
            validate_form(loads("(frobnicate msg)"), _BUILTINS, scope={"msg", "self"})

    def test_rejects_undefined_symbol(self):
        with pytest.raises(EvalError, match="undefined symbol: foo"):
            validate_form(loads("(addressed-to-me? foo)"), _BUILTINS, scope={"msg", "self"})

    def test_let_extends_scope(self):
        validate_form(loads("(let [x msg] (addressed-to-me? x))"), _BUILTINS, scope={"msg"})
        with pytest.raises(EvalError, match="undefined symbol: y"):
            validate_form(loads("(let [x 1] (addressed-to-me? y))"), _BUILTINS, scope={"msg"})


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
        for source in ("(def x 1)", "(lambda (x) x)"):
            with pytest.raises(EvalError, match="unknown"):
                eval_form(loads(source), {}, _BUILTINS)

    def test_recur_alone_still_rejected(self):
        with pytest.raises(EvalError, match="recur outside while"):
            eval_form(loads("(recur 1)"), {}, _BUILTINS)

    def test_while_true_now_reports_bindings(self):
        # Previously "unknown function or special form: while"; now while is
        # a special form and the shape error is about its binding vector.
        with pytest.raises(EvalError, match="while bindings must be a vector"):
            validate_form(loads("(while true 1)"), _BUILTINS, scope=set())
