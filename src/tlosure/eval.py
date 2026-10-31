"""Restricted evaluator for the agent behavior DSL (L1, phase 3a).

Evaluates parsed EDN Forms (``tlosure/edn.py``) under a fuel-bounded
semantics (design decision 3 in ``docs/design/dsl.md``, revised
in phase D — the original "deliberately non-Turing-complete" stance was
superseded once bounded loops entered the language — and again by
ADR 0021, which gave the language full function capability):

- special forms are a closed whitelist: ``if`` / ``when`` / ``let`` /
  ``and`` / ``or`` / ``while`` / ``recur`` / ``defn`` / ``fn`` — no ``def``, no macros, no mutable
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
  enclosing loop context for the duration of the call. Ordinary collection
  functions ``map`` / ``filter`` / ``reduce`` request callbacks through
  the same kernel; each callback application consumes one fuel unit,
  in addition to evaluating its body;
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

The shared kernel suspends before invoking an impure host builtin.
Synchronous evaluation invokes it directly and returns its declared data;
asynchronous evaluation delegates the call to a host handler and resumes
with the actual result. The language itself owns no I/O implementation.

Environment values are opaque to expressions: there is no attribute or
method access syntax, so a host object in ``env`` can only be inspected
through builtins the host registered. What the env provides (sections
like ``self`` / ``msg`` / ``event``) is the host's contract, not the
language's (ADR 0018 修订).
"""

from collections.abc import Awaitable, Callable, Generator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from tlosure.builtins import (
    Builtin,
    BuiltinCall,
    BuiltinFunction,
    Closure,
    EvalDepthError,
    EvalError,
    FuelExhaustedError,
    FunctionCall,
)
from tlosure.edn import Form, Symbol, dumps
from tlosure.validate import SPECIAL_FORMS, _binding_names, _params, validate_form

__all__ = [
    "DEFAULT_FUEL",
    "DEFAULT_MAX_DEPTH",
    "MAX_FUEL",
    "SPECIAL_FORMS",
    "Builtin",
    "BuiltinCall",
    "Closure",
    "EvalDepthError",
    "EvalError",
    "Evaluator",
    "FuelExhaustedError",
    "eval_form",
    "validate_form",
]

DEFAULT_FUEL = 1000
DEFAULT_MAX_DEPTH = 100
MAX_FUEL = 400_000


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


@dataclass
class _Fuel:
    remaining: int


_CURRENT_EVALUATION: ContextVar[tuple["Evaluator", int] | None] = ContextVar(
    "tlosure_evaluation", default=None
)


def current_evaluation() -> tuple["Evaluator", int] | None:
    """Return the active language budget and call depth for a host boundary."""
    return _CURRENT_EVALUATION.get()


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
        self._fuel = _Fuel(fuel)
        self._max_depth = max_depth
        # Frame per enclosing while: the binding names its recur rebinds.
        self._loop_stack: list[list[str]] = []

    @property
    def builtins(self) -> dict[str, Builtin]:
        """Snapshot the vocabulary used by this evaluation."""
        return dict(self._builtins)

    @property
    def budget(self) -> int:
        """The fuel budget this evaluator was launched with."""
        return self._budget

    @property
    def fuel_remaining(self) -> int:
        """Fuel left; negative after exhaustion (the step that ran out)."""
        return self._fuel.remaining

    def fork(self, builtins: dict[str, Builtin]) -> "Evaluator":
        """Use a distinct vocabulary while retaining this evaluation's fuel/depth cap."""
        evaluator = Evaluator(builtins, self._budget, self._max_depth)
        evaluator._fuel = self._fuel
        return evaluator

    def eval(self, value: Any, env: dict[str, Any], depth: int) -> Any:
        """Drive the shared kernel synchronously, preserving builtin return data."""
        program = self._eval(value, env, depth)
        result: Any = None
        try:
            while True:
                try:
                    call = program.send(result)
                except StopIteration as done:
                    return done.value
                try:
                    token = _CURRENT_EVALUATION.set((self, call.depth))
                    try:
                        result = call.invoke()
                    finally:
                        _CURRENT_EVALUATION.reset(token)
                except EvalError:
                    raise
                except Exception as error:
                    raise EvalError(f"{call.name} failed: {error}") from error
        finally:
            program.close()

    async def eval_async(
        self,
        value: Any,
        env: dict[str, Any],
        depth: int,
        handler: Callable[[BuiltinCall], Awaitable[Any]],
    ) -> Any:
        """Resume impure host calls with results supplied by an async handler.

        Closure-backed words always run through the kernel, regardless of
        their purity marker. Cancellation closes suspended frames so loop
        and function scopes cannot leak into later evaluations.
        """
        program = self._eval(value, env, depth)
        result: Any = None
        try:
            while True:
                try:
                    call = program.send(result)
                except StopIteration as done:
                    return done.value
                # Host failures retain their type: the runtime distinguishes
                # retryable I/O from invalid expressions. Pure builtin errors
                # are still normalized by the shared kernel.
                token = _CURRENT_EVALUATION.set((self, call.depth))
                try:
                    result = await handler(call)
                finally:
                    _CURRENT_EVALUATION.reset(token)
        finally:
            program.close()

    def _eval(
        self, value: Any, env: dict[str, Any], depth: int
    ) -> Generator[BuiltinCall, Any, Any]:
        if depth > self._max_depth:
            raise EvalDepthError(f"evaluation depth exceeds {self._max_depth} at {dumps(value)}")
        self._fuel.remaining -= 1
        if self._fuel.remaining < 0:
            raise FuelExhaustedError(
                f"fuel exhausted (budget {self._budget}) while evaluating {dumps(value)}",
                budget=self._budget,
            )
        if isinstance(value, Form):
            return (yield from self._eval_form(value, env, depth))
        if isinstance(value, Symbol):
            name = str(value)
            if name in env:
                return env[name]
            if name in self._builtins and name not in SPECIAL_FORMS:
                return BuiltinFunction(name, self._builtins[name])
            raise EvalError(f"undefined symbol: {name}")
        if isinstance(value, list):
            items: list[Any] = []
            for item in value:
                items.append((yield from self._eval(item, env, depth + 1)))
            return items
        if isinstance(value, dict):
            # map keys stay literal (keywords in practice); values evaluate
            mapping: dict[Any, Any] = {}
            for key, item in value.items():
                mapping[key] = yield from self._eval(item, env, depth + 1)
            return mapping
        # nil / bool / number / string / Keyword: self-evaluating
        return value

    def _eval_form(
        self, form: Form, env: dict[str, Any], depth: int
    ) -> Generator[BuiltinCall, Any, Any]:
        if not form:
            raise EvalError("cannot evaluate empty form ()")
        head = form[0]
        if isinstance(head, Symbol) and str(head) in SPECIAL_FORMS:
            return (yield from self._eval_special(str(head), form, env, depth))
        if isinstance(head, Symbol):
            name = str(head)
            if name in env:
                target = env[name]
            elif name in self._builtins:
                target = BuiltinFunction(name, self._builtins[name])
            else:
                raise EvalError(f"unknown function: {name} (in form {dumps(form)})")
        elif isinstance(head, Form):
            target = yield from self._eval(head, env, depth + 1)
        else:
            raise EvalError(f"form head must be a symbol, got {dumps(head)}")
        if not isinstance(target, (Closure, BuiltinFunction)):
            raise EvalError(f"{dumps(head)} is not callable (in form {dumps(form)})")
        args = yield from self._eval_values(form[1:], env, depth)
        return (yield from self._call(target, args, env, depth))

    def _call(
        self, target: Closure | BuiltinFunction, args: list[Any], env: dict[str, Any], depth: int
    ) -> Generator[BuiltinCall, Any, Any]:
        """Apply any language function, including requested callbacks."""
        if depth > self._max_depth:
            raise EvalDepthError(f"evaluation depth exceeds {self._max_depth} at function call")
        if isinstance(target, Closure):
            return (yield from self._apply(target, args, depth))
        name, builtin = target.name, target.builtin
        if builtin.closure is not None:
            return (yield from self._apply(builtin.closure, args, depth))
        fn = builtin.fn
        assert fn is not None
        if not builtin.pure:
            return (yield BuiltinCall(name=name, builtin=builtin, args=args, env=env, depth=depth))
        token = _CURRENT_EVALUATION.set((self, depth))
        try:
            result = fn(env, *args)
        except EvalError:
            raise
        except Exception as error:
            raise EvalError(f"{name} failed: {error}") from error
        finally:
            _CURRENT_EVALUATION.reset(token)
        if not isinstance(result, Generator):
            return result
        response: Any = None
        try:
            while True:
                token = _CURRENT_EVALUATION.set((self, depth))
                try:
                    request = result.send(response)
                except StopIteration as done:
                    return done.value
                except EvalError:
                    raise
                except Exception as error:
                    raise EvalError(f"{name} failed: {error}") from error
                finally:
                    _CURRENT_EVALUATION.reset(token)
                if not isinstance(request, FunctionCall):
                    raise EvalError(f"{name} yielded an invalid function call")
                self._fuel.remaining -= 1
                if self._fuel.remaining < 0:
                    raise FuelExhaustedError(
                        f"fuel exhausted (budget {self._budget}) while applying callback",
                        budget=self._budget,
                    )
                response = yield from self._call(request.target, request.args, env, depth + 1)
        finally:
            result.close()

    def _apply(
        self, closure: Closure, args: list[Any], depth: int
    ) -> Generator[BuiltinCall, Any, Any]:
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
                result = yield from self._eval(body_form, local, depth + 1)
            return result
        finally:
            self._loop_stack = saved_loop_stack

    def _eval_special(
        self, name: str, form: Form, env: dict[str, Any], depth: int
    ) -> Generator[BuiltinCall, Any, Any]:
        args = form[1:]
        if name == "quote":
            if len(args) != 1:
                raise EvalError(f"quote expects 1 argument, got {len(args)}")
            return args[0]
        if name == "if":
            if len(args) not in (2, 3):
                raise EvalError(f"if expects 2 or 3 arguments, got {len(args)}")
            if (yield from self._eval(args[0], env, depth + 1)):
                return (yield from self._eval(args[1], env, depth + 1))
            return (yield from self._eval(args[2], env, depth + 1)) if len(args) == 3 else None
        if name == "when":
            if not args:
                raise EvalError("when expects a test form")
            if not (yield from self._eval(args[0], env, depth + 1)):
                return None
            return (yield from self._eval_body(args[1:], env, depth))
        if name == "let":
            if len(args) < 2:
                raise EvalError("let expects a binding vector and at least one body form")
            local = yield from self._eval_bindings(args[0], env, depth)
            return (yield from self._eval_body(args[1:], local, depth))
        if name == "and":
            result: Any = True
            for arg in args:
                result = yield from self._eval(arg, env, depth + 1)
                if not result:
                    return result
            return result
        if name == "or":
            result = None
            for arg in args:
                result = yield from self._eval(arg, env, depth + 1)
                if result:
                    return result
            return result
        if name == "while":
            if len(args) < 2:
                raise EvalError("while expects a binding vector and a test")
            names = _binding_names(args[0], "while")
            local = yield from self._eval_bindings(args[0], env, depth)
            test, body = args[1], args[2:]
            self._loop_stack.append(names)
            try:
                while True:
                    try:
                        if not (yield from self._eval(test, local, depth + 1)):
                            return None
                        last: Any = None
                        for form in body:
                            last = yield from self._eval(form, local, depth + 1)
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
            new_vals = yield from self._eval_values(args, env, depth)
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
        raise EvalError(f"unknown special form: {name}")

    def _eval_bindings(
        self, bindings: Any, env: dict[str, Any], depth: int
    ) -> Generator[BuiltinCall, Any, dict[str, Any]]:
        names = _binding_names(bindings, "let")
        local = dict(env)
        for name, init in zip(names, bindings[1::2], strict=True):
            local[name] = yield from self._eval(init, local, depth + 1)
        return local

    def _eval_body(
        self, body: list[Any], env: dict[str, Any], depth: int
    ) -> Generator[BuiltinCall, Any, Any]:
        result: Any = None
        for form in body:
            result = yield from self._eval(form, env, depth + 1)
        return result

    def _eval_values(
        self, values: list[Any], env: dict[str, Any], depth: int
    ) -> Generator[BuiltinCall, Any, list[Any]]:
        result: list[Any] = []
        for value in values:
            result.append((yield from self._eval(value, env, depth + 1)))
        return result
