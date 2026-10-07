"""Restricted evaluator for the agent behavior DSL (L1, phase 3a).

Evaluates parsed EDN Forms (``tlosure/edn.py``) under a fuel-bounded
semantics (design decision 3 in ``docs/design/dsl.md``, revised
in phase D — the original "deliberately non-Turing-complete" stance was
superseded once bounded loops entered the language — and again by
ADR 0021, which gave the language full function capability):

- special forms are a closed whitelist: ``if`` / ``when`` / ``let`` /
  ``and`` / ``or`` / ``while`` / ``recur`` / ``map`` / ``filter`` /
  ``reduce`` / ``defn`` / ``fn`` — no ``def``, no macros, no mutable
  global state. ``defn`` defines a named function anywhere a body form
  is allowed (the name is bound into the *current* environment dict, so
  recursion and later sibling forms both resolve it); ``fn`` evaluates
  to an anonymous :class:`Closure`. Closures capture the definition-time
  environment by reference, support variadic parameters
  (``[a b & rest]``), and are first-class values (passed as arguments,
  returned from functions, applied via a Symbol head or a Form head);
  function bodies may call action builtins like any other position;
- ``while`` / ``recur`` (phase D) are the Clojure-style bounded loop
  pair: state flows through the binding vector, ``recur`` rebinds and
  jumps back to the loop head, and is only legal in a ``while`` body's
  tail position (``if`` branches, trailing ``when``/``let`` bodies, and
  the final ``and``/``or`` argument inherit tail position). ``recur``
  never crosses a function boundary: applying a closure clears the
  enclosing loop context for the duration of the call. ``map`` /
  ``filter`` / ``reduce`` iterate a vector with an explicitly bound
  element variable (no lambda needed; closures also compose with them).
  Every iteration is still metered — each step consumes one fuel unit —
  so every loop is bounded by fuel;
- every other form head must name a registered :class:`Builtin` or
  resolve to a :class:`Closure` in the environment — a Symbol head
  resolves special form → environment → builtin registry, in that order;
- maps are data: keys stay literal (keywords in practice), values are
  evaluated — so action option maps may embed expressions, e.g.
  ``{:agent (get event :recipient)}``;
- evaluation is metered: each evaluation step consumes one unit of
  *fuel* (:class:`FuelExhaustedError` when it runs out) and form nesting
  is capped (:class:`EvalDepthError`), so evaluation always terminates.
  Recursion is constrained by the same two levers: nested closure
  application draws fuel and depth like any other evaluation.

Billing model (the precise contract):

- one *step* = one evaluated node: a self-evaluating literal, a symbol
  resolution, each element of a vector/map, a builtin call, and each
  special-form subexpression that actually evaluates. Untaken branches
  (``if``/``when``/``and``/``or`` short-circuit) consume nothing;
- builtin *internals* are unmetered — a builtin's own computation is the
  host's resource domain, not the language's. A closure-backed registry
  entry (a data-defined word, :class:`Builtin` with ``closure`` set) is
  the opposite: the entry is applied like any closure, so its body
  evaluates through the kernel and draws fuel and depth from the
  caller's budget;
- the budget is a hard language-level invariant: ``fuel`` must lie in
  ``[0, MAX_FUEL]`` (:class:`ValueError` otherwise), so no caller can
  launch an effectively unbounded evaluation;
- :class:`FuelExhaustedError` carries the ``budget`` the run was
  launched with, for structured host observability;
- one :class:`Evaluator` instance = one budget. Hosts evaluating several
  forms under a combined budget (e.g. a rule's gate + actions) share one
  instance; single-form evaluation goes through :func:`eval_form`.

The module is the evaluator kernel only: the contract types and the
general builtin vocabulary live in ``tlosure/builtins.py``, static
validation in ``tlosure/validate.py`` (:func:`validate_form`); the
contract types and ``validate_form`` are re-exported here so existing
imports keep working.

Action expressions keep one rule: evaluation never performs side
effects. Action builtins are registered by the host (tlon), marked
``pure=False``; they validate their arguments and produce an
:class:`ActionRequest` — the engine executes the requests after
evaluation completes ("the DSL describes, the engine executes").

Environment values are opaque to expressions: there is no attribute or
method access syntax, so a host object in ``env`` can only be inspected
through builtins the host registered. What the env provides (sections
like ``self`` / ``msg`` / ``event``) is the host's contract, not the
language's (ADR 0018 修订).
"""

from typing import Any

from tlosure.builtins import (
    ActionRequest,
    Builtin,
    Closure,
    EvalDepthError,
    EvalError,
    FuelExhaustedError,
)
from tlosure.edn import Form, Symbol, dumps
from tlosure.validate import _binding_names, _params, validate_form

__all__ = [
    "DEFAULT_FUEL",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_WHEN_FUEL",
    "MAX_FUEL",
    "SPECIAL_FORMS",
    "ActionRequest",
    "Builtin",
    "Closure",
    "EvalDepthError",
    "EvalError",
    "Evaluator",
    "FuelExhaustedError",
    "eval_form",
    "validate_form",
]

DEFAULT_FUEL = 1000
DEFAULT_WHEN_FUEL = 200
DEFAULT_MAX_DEPTH = 100
MAX_FUEL = 10000

SPECIAL_FORMS = frozenset(
    {
        "if",
        "when",
        "let",
        "and",
        "or",
        "while",
        "recur",
        "map",
        "filter",
        "reduce",
        "defn",
        "fn",
    }
)


class _Recur(Exception):  # noqa: N818 — a control-flow signal, not an error
    """Internal control-flow signal: a while body requested another iteration.

    Raised by the ``recur`` special form with the rebound environment;
    caught by the innermost enclosing ``while`` to jump back to its loop
    head. Never escapes to a caller — ``eval_form`` converts a leaked
    signal into an :class:`EvalError`.
    """

    def __init__(self, env: dict[str, Any]):
        self.env = env
        super().__init__("recur")


def eval_form(
    form: Any,
    env: dict[str, Any],
    builtins: dict[str, Builtin],
    fuel: int = DEFAULT_FUEL,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> Any:
    """Evaluate a parsed EDN value under the restricted semantics.

    Args:
        form: parsed EDN data (Form/Symbol/vector/literal).
        env: symbol bindings (e.g. ``{"msg": message, "self": {...}}``).
        builtins: allowed functions, keyed by name.
        fuel: evaluation step budget; must lie in ``[0, MAX_FUEL]``.
        max_depth: form nesting depth cap; must be at least 1.

    Raises:
        ValueError: fuel outside ``[0, MAX_FUEL]`` or max_depth < 1.
        EvalError: on any evaluation failure (unknown symbol/form,
            builtin error, fuel exhaustion, depth overflow).
    """
    try:
        return Evaluator(builtins, fuel, max_depth).eval(form, dict(env), 0)
    except _Recur as e:
        # Defensive: a hand-built (unvalidated) form can never let _Recur
        # escape a while; if one does, report it as a plain eval error.
        raise EvalError("recur outside while") from e


class Evaluator:
    """Stateful single-run evaluator: one instance = one fuel budget.

    Single-form evaluation normally goes through :func:`eval_form`; use
    this class directly when several forms must share one combined
    budget (e.g. a host rule whose gate and actions draw from the same
    allowance) — each ``eval`` call keeps drawing down the same counter.

    The constructor enforces the language-level hard caps, so the
    invariant "no evaluation exceeds ``MAX_FUEL`` steps" holds no matter
    which entry point a host picks.
    """

    def __init__(self, builtins: dict[str, Builtin], fuel: int, max_depth: int):
        if not 0 <= fuel <= MAX_FUEL:
            raise ValueError(f"fuel must lie in [0, {MAX_FUEL}], got {fuel}")
        if max_depth < 1:
            raise ValueError(f"max_depth must be at least 1, got {max_depth}")
        self._builtins = builtins
        self._budget = fuel
        self._fuel = fuel
        self._max_depth = max_depth
        # Frame per enclosing while: the binding names its recur rebinds.
        self._loop_stack: list[list[str]] = []

    @property
    def budget(self) -> int:
        """The fuel budget this evaluator was launched with."""
        return self._budget

    @property
    def fuel_remaining(self) -> int:
        """Fuel left; negative after exhaustion (the step that ran out)."""
        return self._fuel

    def eval(self, value: Any, env: dict[str, Any], depth: int) -> Any:
        if depth > self._max_depth:
            raise EvalDepthError(f"evaluation depth exceeds {self._max_depth} at {dumps(value)}")
        self._fuel -= 1
        if self._fuel < 0:
            raise FuelExhaustedError(
                f"fuel exhausted (budget {self._budget}) while evaluating {dumps(value)}",
                budget=self._budget,
            )
        if isinstance(value, Form):
            return self._eval_form(value, env, depth)
        if isinstance(value, Symbol):
            name = str(value)
            if name not in env:
                raise EvalError(f"undefined symbol: {name}")
            return env[name]
        if isinstance(value, list):
            return [self.eval(item, env, depth + 1) for item in value]
        if isinstance(value, dict):
            # map keys stay literal (keywords in practice); values evaluate
            return {key: self.eval(item, env, depth + 1) for key, item in value.items()}
        # nil / bool / number / string / Keyword: self-evaluating
        return value

    def _eval_form(self, form: Form, env: dict[str, Any], depth: int) -> Any:
        if not form:
            raise EvalError("cannot evaluate empty form ()")
        head = form[0]
        if not isinstance(head, Symbol):
            # A Form head is a function-valued expression applied to the
            # arguments, e.g. ((fn [x] (inc x)) 5); anything else is a
            # shape error.
            if not isinstance(head, Form):
                raise EvalError(f"form head must be a symbol, got {dumps(head)}")
            fn_value = self.eval(head, env, depth + 1)
            if not isinstance(fn_value, Closure):
                raise EvalError(f"form head is not callable: {dumps(head)}")
            args = [self.eval(arg, env, depth + 1) for arg in form[1:]]
            return self._apply(fn_value, args, depth)
        name = str(head)
        if name in SPECIAL_FORMS:
            return self._eval_special(name, form, env, depth)
        if name in env:
            # Environment bindings shadow builtins (ADR 0021 head
            # resolution: special form → env Closure → builtin).
            bound = env[name]
            if not isinstance(bound, Closure):
                raise EvalError(f"{name} is not callable (in form {dumps(form)})")
            args = [self.eval(arg, env, depth + 1) for arg in form[1:]]
            return self._apply(bound, args, depth)
        builtin = self._builtins.get(name)
        if builtin is None:
            raise EvalError(f"unknown function: {name} (in form {dumps(form)})")
        args = [self.eval(arg, env, depth + 1) for arg in form[1:]]
        if builtin.closure is not None:
            # Data-defined word (closure-backed entry): applied like any
            # closure — the body draws fuel and depth from this run,
            # unlike a host builtin's unmetered internals.
            return self._apply(builtin.closure, args, depth)
        fn = builtin.fn
        assert fn is not None  # Builtin.__post_init__: exactly one of fn / closure
        try:
            return fn(env, *args)
        except EvalError:
            raise
        except Exception as e:
            raise EvalError(f"{name} failed: {e} (in form {dumps(form)})") from e

    def _apply(self, closure: Closure, args: list[Any], depth: int) -> Any:
        """Apply a closure: arity check, bind parameters, run the body.

        The body evaluates in ``dict(closure.env)`` extended with the
        parameter bindings; every nested application goes back through
        :meth:`eval`, so fuel draw-down and the depth cap bound recursion
        like any other evaluation. ``recur`` never crosses a function
        boundary (Clojure semantics): the enclosing loop stack is saved
        and cleared for the duration of the call.
        """
        label = closure.name or "anonymous fn"
        n_fixed = len(closure.params)
        if closure.rest is None:
            if len(args) != n_fixed:
                raise EvalError(f"{label} expects {n_fixed} arguments, got {len(args)}")
        elif len(args) < n_fixed:
            raise EvalError(f"{label} expects at least {n_fixed} arguments, got {len(args)}")
        local = dict(closure.env)
        for param, arg in zip(closure.params, args, strict=False):
            local[param] = arg
        if closure.rest is not None:
            local[closure.rest] = args[n_fixed:]
        saved_loop_stack = self._loop_stack
        self._loop_stack = []
        try:
            result: Any = None
            for body_form in closure.body:
                result = self.eval(body_form, local, depth + 1)
            return result
        finally:
            self._loop_stack = saved_loop_stack

    def _eval_special(self, name: str, form: Form, env: dict[str, Any], depth: int) -> Any:
        args = form[1:]
        if name == "if":
            if len(args) not in (2, 3):
                raise EvalError(f"if expects 2 or 3 arguments, got {len(args)}")
            if self.eval(args[0], env, depth + 1):
                return self.eval(args[1], env, depth + 1)
            return self.eval(args[2], env, depth + 1) if len(args) == 3 else None
        if name == "when":
            if not args:
                raise EvalError("when expects a test form")
            if not self.eval(args[0], env, depth + 1):
                return None
            return self._eval_body(args[1:], env, depth)
        if name == "let":
            if len(args) < 2:
                raise EvalError("let expects a binding vector and at least one body form")
            local = self._eval_bindings(args[0], env, depth)
            return self._eval_body(args[1:], local, depth)
        if name == "and":
            result: Any = True
            for arg in args:
                result = self.eval(arg, env, depth + 1)
                if not result:
                    return result
            return result
        if name == "or":
            result = None
            for arg in args:
                result = self.eval(arg, env, depth + 1)
                if result:
                    return result
            return result
        if name == "while":
            if len(args) < 2:
                raise EvalError("while expects a binding vector and a test")
            names = _binding_names(args[0], "while")
            local = self._eval_bindings(args[0], env, depth)
            test, body = args[1], args[2:]
            self._loop_stack.append(names)
            try:
                while True:
                    try:
                        if not self.eval(test, local, depth + 1):
                            return None
                        last: Any = None
                        for form in body:
                            last = self.eval(form, local, depth + 1)
                        return last
                    except _Recur as r:
                        # Rebinding with the recurred values, loop again.
                        local = r.env
            finally:
                self._loop_stack.pop()
        if name == "recur":
            if not self._loop_stack:
                raise EvalError("recur outside while")
            names = self._loop_stack[-1]
            if len(args) != len(names):
                raise EvalError(f"recur expects {len(names)} arguments, got {len(args)}")
            # All recur arguments evaluate in the old env, then rebind
            # simultaneously (Clojure semantics).
            new_vals = [self.eval(arg, env, depth + 1) for arg in args]
            new_local = dict(env)
            for nm, val in zip(names, new_vals, strict=True):
                new_local[nm] = val
            raise _Recur(new_local)
        if name == "defn":
            if len(args) < 3:
                raise EvalError(
                    "defn expects a name, a parameter vector, and at least one body form"
                )
            fn_name = args[0]
            if not isinstance(fn_name, Symbol):
                raise EvalError(f"defn name must be a symbol, got {dumps(fn_name)}")
            params, rest = _params(args[1], "defn")
            # Capture the current env dict *by reference* and write the
            # closure into it: the body's self-reference resolves through
            # the same object (recursion), and later sibling body forms
            # share the dict, so they see the definition too.
            closure = Closure(
                params=params, rest=rest, body=list(args[2:]), env=env, name=str(fn_name)
            )
            env[str(fn_name)] = closure
            return closure
        if name == "fn":
            if len(args) < 2:
                raise EvalError("fn expects a parameter vector and at least one body form")
            params, rest = _params(args[0], "fn")
            return Closure(params=params, rest=rest, body=list(args[1:]), env=env)
        if name in ("map", "filter"):
            if len(args) != 3:
                raise EvalError(f"{name} expects a binding, a body, and a collection")
            binder = args[0]
            if not isinstance(binder, Symbol):
                raise EvalError(f"{name} binding must be a symbol, got {dumps(binder)}")
            coll = self.eval(args[2], env, depth + 1)
            if isinstance(coll, Form) or not isinstance(coll, list):
                raise EvalError(f"{name} expects a vector collection, got {dumps(coll)}")
            body = args[1]
            out: list[Any] = []
            for item in coll:
                item_env = dict(env)
                item_env[str(binder)] = item
                value = self.eval(body, item_env, depth + 1)
                if name == "map":
                    out.append(value)
                elif value:
                    out.append(item)
            return out
        if name == "reduce":
            if len(args) != 5:
                raise EvalError("reduce expects accumulator, element, body, init, and collection")
            acc_name, x_name = args[0], args[1]
            if not isinstance(acc_name, Symbol) or not isinstance(x_name, Symbol):
                raise EvalError("reduce bindings must be symbols")
            acc = self.eval(args[3], env, depth + 1)
            coll = self.eval(args[4], env, depth + 1)
            if isinstance(coll, Form) or not isinstance(coll, list):
                raise EvalError(f"reduce expects a vector collection, got {dumps(coll)}")
            for item in coll:
                item_env = dict(env)
                item_env[str(acc_name)] = acc
                item_env[str(x_name)] = item
                acc = self.eval(args[2], item_env, depth + 1)
            return acc
        raise EvalError(f"unknown special form: {name}")

    def _eval_bindings(self, bindings: Any, env: dict[str, Any], depth: int) -> dict[str, Any]:
        names = _binding_names(bindings, "let")
        local = dict(env)
        for name, init in zip(names, bindings[1::2], strict=True):
            local[name] = self.eval(init, local, depth + 1)
        return local

    def _eval_body(self, body: list[Any], env: dict[str, Any], depth: int) -> Any:
        result: Any = None
        for form in body:
            result = self.eval(form, env, depth + 1)
        return result
