"""Closure-backed registry entries (data-defined words).

A ``Builtin`` carrying a ``Closure`` instead of a host ``fn`` is applied
like any closure: arity is checked by ``_apply``, the body resolves
words through the caller's registry, and — the substantive semantic
difference from a host builtin — the body's evaluation draws fuel and
depth from the caller's budget. The entry's captured env is exactly
what the closure was registered with; an empty env means the body sees
its arguments and nothing else (zero injection).
"""

from typing import Any

import pytest

from tlosure.builtins import (
    ARITHMETIC_BUILTINS,
    CORE_BUILTINS,
    Builtin,
    Closure,
    EvalError,
)
from tlosure.edn import Form, loads
from tlosure.eval import Evaluator

_BUILTINS: dict[str, Builtin] = {**CORE_BUILTINS, **ARITHMETIC_BUILTINS}


def _word(impl_source: str, *, pure: bool = True, name: str | None = None) -> Builtin:
    """A closure-backed entry from a ``(fn [params] body...)`` literal."""
    impl = loads(impl_source)
    assert isinstance(impl, Form)
    params = [str(p) for p in impl[1]]
    closure = Closure(params=params, rest=None, body=list(impl[2:]), env={}, name=name)
    return Builtin.from_closure(closure, pure=pure)


def _run(source: str, builtins: dict[str, Builtin], env: dict[str, Any] | None = None) -> Any:
    return Evaluator(builtins, 100, 100).eval(loads(source), dict(env or {}), 0)


class TestShape:
    def test_from_closure_sets_closure_not_fn(self):
        entry = _word("(fn [x] (inc x))", pure=False)
        assert entry.fn is None
        assert entry.closure is not None
        assert entry.pure is False

    def test_exactly_one_of_fn_and_closure(self):
        with pytest.raises(ValueError, match="exactly one"):
            Builtin()
        with pytest.raises(ValueError, match="exactly one"):
            Builtin(
                fn=lambda env: None,
                closure=Closure(params=[], rest=None, body=[1], env={}),
            )


class TestEvaluation:
    def test_call(self):
        builtins = {**_BUILTINS, "double": _word("(fn [x] (* x 2))", name="double")}
        assert _run("(double 21)", builtins) == 42

    def test_arity_error_names_the_word(self):
        builtins = {**_BUILTINS, "double": _word("(fn [x] (* x 2))", name="double")}
        with pytest.raises(EvalError, match="double expects 1 arguments, got 2"):
            _run("(double 1 2)", builtins)

    def test_body_calls_host_builtins_via_caller_registry(self):
        builtins = {
            **_BUILTINS,
            "shout": _word('(fn [s] (str s "!"))', name="shout"),
        }
        assert _run('(shout "hi")', builtins) == "hi!"

    def test_nested_closure_backed_application(self):
        """A closure-backed word calling another closure-backed word: both
        bodies go through ``_apply`` and draw from the one budget."""
        builtins = {
            **_BUILTINS,
            "double": _word("(fn [x] (* x 2))", name="double"),
            "quadruple": _word("(fn [x] (double (double x)))", name="quadruple"),
        }
        assert _run("(quadruple 3)", builtins) == 12

    def test_body_may_apply_anonymous_closures(self):
        builtins = {
            **_BUILTINS,
            "apply-inc": _word("(fn [x] ((fn [y] (inc y)) x))", name="apply-inc"),
        }
        assert _run("(apply-inc 41)", builtins) == 42


class TestFuelAccounting:
    """The semantic-difference anchor: the same computation consumes fuel
    as a closure-backed word and none (beyond the call node itself) as a
    host builtin."""

    def test_closure_backed_body_draws_fuel_host_builtin_does_not(self):
        def host_double(env: dict[str, Any], x: Any) -> Any:
            return x * 2

        host = Evaluator({**_BUILTINS, "double": Builtin(host_double)}, 100, 100)
        assert host.eval(loads("(double 21)"), {}, 0) == 42
        host_spent = 100 - host.fuel_remaining

        data = Evaluator(
            {**_BUILTINS, "double": _word("(fn [x] (* x 2))", name="double")}, 100, 100
        )
        assert data.eval(loads("(double 21)"), {}, 0) == 42
        data_spent = 100 - data.fuel_remaining

        # The call node + the argument cost the same either way; the
        # closure-backed body (* x 2): form + symbol + literal = 3 extra.
        assert host_spent == 2
        assert data_spent == host_spent + 3

    def test_fuel_exhaustion_bounds_the_body(self):
        from tlosure.builtins import FuelExhaustedError

        builtins = {
            **_BUILTINS,
            "loop": _word(
                "(fn [n] (while [i 0] (< i n) (recur (inc i))))", name="loop"
            ),
        }
        evaluator = Evaluator(builtins, 20, 100)
        with pytest.raises(FuelExhaustedError):
            evaluator.eval(loads("(loop 1000)"), {}, 0)


class TestZeroInjection:
    def test_captured_env_is_empty(self):
        entry = _word("(fn [x] (inc x))")
        assert entry.closure is not None
        assert entry.closure.env == {}

    def test_body_cannot_see_caller_env(self):
        """A symbol bound in the caller's env is invisible inside the body —
        the closure captured nothing, so the body sees only its params."""
        builtins = {**_BUILTINS, "leak": _word("(fn [] secret)", name="leak")}
        with pytest.raises(EvalError, match="undefined symbol: secret"):
            _run("(leak)", builtins, env={"secret": 42})
