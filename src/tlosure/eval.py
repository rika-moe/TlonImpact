"""Restricted evaluator for the agent behavior DSL (L1, phase 3a).

Evaluates parsed EDN Forms (``tlosure/edn.py``) under a fuel-bounded
semantics (design decision 3 in ``docs/design/dsl.md``, revised
in phase D — the original "deliberately non-Turing-complete" stance was
superseded once bounded loops entered the language):

- special forms are a closed whitelist: ``if`` / ``when`` / ``let`` /
  ``and`` / ``or`` / ``while`` / ``recur`` / ``map`` / ``filter`` /
  ``reduce`` — no ``def``, no ``lambda``/``fn``, no macros, no mutable
  global state. Recursion is impossible (there is no way to name a
  function), but ``while``/``recur`` express general iteration, so the
  language is Turing-complete modulo the fuel budget — fuel, not the
  absence of loops, is the termination guarantee;
- ``while`` / ``recur`` (phase D) are the Clojure-style bounded loop
  pair: state flows through the binding vector, ``recur`` rebinds and
  jumps back to the loop head, and is only legal in a ``while`` body's
  tail position (``if`` branches, trailing ``when``/``let`` bodies, and
  the final ``and``/``or`` argument inherit tail position). ``map`` /
  ``filter`` / ``reduce`` iterate a vector with an explicitly bound
  element variable (no lambda). Every iteration is still metered — each
  step consumes one fuel unit — so every loop is bounded by fuel;
- every other form head must name a registered :class:`Builtin`;
- maps are data: keys stay literal (keywords in practice), values are
  evaluated — so action option maps may embed expressions, e.g.
  ``{:agent (get event :recipient)}``;
- evaluation is metered: each evaluation step consumes one unit of
  *fuel* (:class:`FuelExhaustedError` when it runs out) and form nesting
  is capped (:class:`EvalDepthError`), so evaluation always terminates.

The module is the evaluator kernel only: the builtin vocabulary and its
registries live in ``tlosure/builtins.py`` (pure predicates/functions
only at module level, so trigger/loop/stage-condition evaluation is
side-effect free by construction), static validation in
``tlosure/validate.py`` (:func:`validate_form`,
:func:`validate_member_action`); both are re-exported here so existing
imports keep working.

Action expressions — on-complete (phase 4b) and script rules (phase P5a)
alike — keep the same rule: evaluation never performs side effects. The
action builtins (``spawn`` / ``emit`` / ``start`` / ``stop`` /
``start-all`` / ``forward``), registered by
:func:`tlosure.builtins.make_member_builtins` (phase C) with
``pure=False``, validate their arguments and produce an
:class:`ActionRequest`; the engine executes the requests after
evaluation completes ("the DSL describes, the engine executes"). Static
validation distinguishes two purity positions
(:func:`validate_member_action`): a ``when`` condition must be pure, an
action position must be an action form with pure arguments. Host
predicates that read registry state (``sleeping?`` / ``running?`` /
``lazy?``) are never registered module-level; the caller closure-injects
them via ``make_member_builtins``.

Environment values are opaque to expressions: there is no attribute or
method access syntax, so a mail object in ``env`` can only be inspected
through builtins (which see it through the structural
:class:`tlosure.builtins.MailView` protocol — ADR 0018 §3). ``env``
also provides ``self`` (``{"id": ..., "name": ..., "address": ...}``),
which builtins read to resolve "me"; the host pre-derives ``address`` so
builtins never call back into the host domain.
"""

from typing import Any

from tlosure.builtins import (
    ActionRequest,
    Builtin,
    EvalDepthError,
    EvalError,
    FuelExhaustedError,
    make_member_builtins,
)
from tlosure.edn import Form, Symbol, dumps
from tlosure.validate import _binding_names, validate_form, validate_member_action

__all__ = [
    "DEFAULT_FUEL",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_WHEN_FUEL",
    "MAX_FUEL",
    "SPECIAL_FORMS",
    "ActionRequest",
    "Builtin",
    "EvalDepthError",
    "EvalError",
    "FuelExhaustedError",
    "eval_form",
    "make_member_builtins",
    "validate_form",
    "validate_member_action",
]

DEFAULT_FUEL = 1000
DEFAULT_WHEN_FUEL = 200
DEFAULT_MAX_DEPTH = 100
MAX_FUEL = 10000

SPECIAL_FORMS = frozenset(
    {"if", "when", "let", "and", "or", "while", "recur", "map", "filter", "reduce"}
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
        fuel: evaluation step budget.
        max_depth: form nesting depth cap.

    Raises:
        EvalError: on any evaluation failure (unknown symbol/form,
            builtin error, fuel exhaustion, depth overflow).
    """
    try:
        return _Evaluator(builtins, fuel, max_depth).eval(form, dict(env), 0)
    except _Recur as e:
        # Defensive: a hand-built (unvalidated) form can never let _Recur
        # escape a while; if one does, report it as a plain eval error.
        raise EvalError("recur outside while") from e


class _Evaluator:
    """Stateful single-run evaluator (fuel counter + depth cap)."""

    def __init__(self, builtins: dict[str, Builtin], fuel: int, max_depth: int):
        self._builtins = builtins
        self._fuel = fuel
        self._max_depth = max_depth
        # Frame per enclosing while: the binding names its recur rebinds.
        self._loop_stack: list[list[str]] = []

    def eval(self, value: Any, env: dict[str, Any], depth: int) -> Any:
        if depth > self._max_depth:
            raise EvalDepthError(
                f"evaluation depth exceeds {self._max_depth} at {dumps(value)}"
            )
        self._fuel -= 1
        if self._fuel < 0:
            raise FuelExhaustedError(f"fuel exhausted while evaluating {dumps(value)}")
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
            raise EvalError(f"form head must be a symbol, got {dumps(head)}")
        name = str(head)
        if name in SPECIAL_FORMS:
            return self._eval_special(name, form, env, depth)
        builtin = self._builtins.get(name)
        if builtin is None:
            raise EvalError(f"unknown function: {name} (in form {dumps(form)})")
        args = [self.eval(arg, env, depth + 1) for arg in form[1:]]
        try:
            return builtin.fn(env, *args)
        except EvalError:
            raise
        except Exception as e:
            raise EvalError(f"{name} failed: {e} (in form {dumps(form)})") from e

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
                raise EvalError(
                    f"recur expects {len(names)} arguments, got {len(args)}"
                )
            # All recur arguments evaluate in the old env, then rebind
            # simultaneously (Clojure semantics).
            new_vals = [self.eval(arg, env, depth + 1) for arg in args]
            new_local = dict(env)
            for nm, val in zip(names, new_vals, strict=True):
                new_local[nm] = val
            raise _Recur(new_local)
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
                raise EvalError(
                    "reduce expects accumulator, element, body, init, and collection"
                )
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
