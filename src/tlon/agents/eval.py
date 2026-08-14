"""Restricted evaluator for the agent behavior DSL (L1, phase 3a).

Evaluates parsed EDN Forms (``agents/edn.py``) under a fuel-bounded
semantics (design decision 3 in ``docs/design/dsl-agents.md``, revised
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
  is capped (:class:`EvalDepthError`), so evaluation always terminates;
- builtins carry a purity marker. The trigger registry
  (:data:`TRIGGER_BUILTINS`) registers pure predicates/functions only —
  side-effecting actions (``spawn`` / ``emit``) are never registered
  here, so trigger evaluation is side-effect free by construction. The
  loop registry (:data:`LOOP_BUILTINS`, phase 7a) likewise registers
  pure termination predicates only, and the stage-condition registry
  (:data:`STAGE_COND_BUILTINS`, phase 7c) registers pure stage-gating
  predicates over loop state and the session scratchpad.

Action expressions — on-complete (phase 4b) and script rules (phase P5a)
alike — keep the same rule: evaluation never performs side effects. All
action builtins (``spawn`` / ``emit`` / ``start`` / ``stop`` /
``start-all`` / ``forward``), registered by :func:`make_member_builtins`
(phase C) with ``pure=False``, validate their arguments and produce an
:class:`ActionRequest`; the engine executes the requests after
evaluation completes ("the DSL describes, the engine executes"). Whether
a request is appended to a collector (on-complete, where ``when`` bodies
may hold several actions) or returned as the action form's value
(deterministic rules) is set by the factory — see
:func:`make_member_builtins`. Static validation distinguishes two purity
positions (:func:`validate_member_action`): a ``when`` condition must be
pure, an action position must be an action form with pure arguments.
Host predicates that read registry state (``sleeping?`` / ``running?`` /
``lazy?``) are never registered module-level; the caller closure-injects
them via :func:`make_member_builtins`.

Environment values are opaque to expressions: there is no attribute or
method access syntax, so a ``Mail`` in ``env`` can only be inspected
through builtins. ``env`` also provides ``self`` (``{"id": ..., "name":
...}``) which builtins read to resolve "me".
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from tlon.agents.edn import Form, Symbol, dumps
from tlon.organization.mail.mail import Mail, address_for_member

DEFAULT_FUEL = 1000
DEFAULT_WHEN_FUEL = 200
DEFAULT_MAX_DEPTH = 100
MAX_FUEL = 10000

SPECIAL_FORMS = frozenset(
    {"if", "when", "let", "and", "or", "while", "recur", "map", "filter", "reduce"}
)


class EvalError(Exception):
    """Any evaluation or static-validation failure of a DSL expression."""


class FuelExhaustedError(EvalError):
    """The evaluation step budget ran out."""


class EvalDepthError(EvalError):
    """The form nesting depth cap was exceeded."""


@dataclass(frozen=True)
class Builtin:
    """A registered builtin function with a purity marker.

    ``fn`` receives ``(env, *evaluated_args)``; ``pure`` marks absence of
    side effects. Only pure builtins may be registered for trigger
    expressions — the registries are the enforcement point.
    """

    fn: Callable[..., Any]
    pure: bool = True


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


@dataclass(frozen=True)
class _WhileCtx:
    """Nearest enclosing ``while`` context threaded through static validation.

    ``arity`` is the loop's binding count (the ``recur`` argument count it
    accepts); ``tail`` marks whether the current position is a tail
    position of that loop (where ``recur`` is legal).
    """

    arity: int
    tail: bool


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


def validate_form(form: Any, builtins: dict[str, Builtin], scope: set[str]) -> None:
    """Statically check an expression without executing it.

    Walks the form verifying that every form head is a whitelisted
    special form or a registered builtin, every symbol resolves in
    ``scope`` (extended by ``let`` bindings), and special-form shapes are
    well-formed. Raises :class:`EvalError` on the first violation.
    """
    _validate(form, builtins, set(scope))


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


def _non_tail(loop: _WhileCtx | None) -> _WhileCtx | None:
    """The same loop, but the current position is not a tail position."""
    return _WhileCtx(loop.arity, False) if loop is not None else None


def _validate_seq(
    forms: list[Any], builtins: dict[str, Builtin], scope: set[str], loop: _WhileCtx | None
) -> None:
    """Validate a body: only its final form may carry the loop's tail slot."""
    n = len(forms)
    for i, form in enumerate(forms):
        sub = loop if (loop is not None and i == n - 1) else _non_tail(loop)
        _validate(form, builtins, scope, sub)


def _binding_names(bindings: Any, what: str) -> list[str]:
    """Extract the names from a ``[name init ...]`` binding vector.

    ``what`` names the construct in error messages ("let" / "while").
    """
    if isinstance(bindings, Form) or not isinstance(bindings, list):
        raise EvalError(f"{what} bindings must be a vector, got {dumps(bindings)}")
    if len(bindings) % 2 != 0:
        raise EvalError(f"{what} bindings must contain an even number of forms")
    names: list[str] = []
    for i in range(0, len(bindings), 2):
        name = bindings[i]
        if not isinstance(name, Symbol):
            raise EvalError(f"{what} binding name must be a symbol, got {dumps(name)}")
        names.append(str(name))
    return names


def _validate(
    value: Any,
    builtins: dict[str, Builtin],
    scope: set[str],
    loop: _WhileCtx | None = None,
) -> None:
    if isinstance(value, Form):
        if not value:
            raise EvalError("empty form () is not a valid expression")
        head = value[0]
        if not isinstance(head, Symbol):
            raise EvalError(f"form head must be a symbol, got {dumps(head)}")
        name = str(head)
        if name == "let":
            _validate_let(value, builtins, scope, loop)
            return
        if name == "while":
            _validate_while(value, builtins, scope, loop)
            return
        if name == "recur":
            _validate_recur(value, builtins, scope, loop)
            return
        if name in ("map", "filter"):
            _validate_map_like(value, builtins, scope, loop)
            return
        if name == "reduce":
            _validate_reduce(value, builtins, scope, loop)
            return
        if name == "if":
            if len(value) < 3 or len(value) > 4:
                raise EvalError(f"if expects 2 or 3 arguments, got {len(value) - 1}")
            # test is not a tail position; then/else inherit it.
            _validate(value[1], builtins, scope, _non_tail(loop))
            _validate(value[2], builtins, scope, loop)
            if len(value) == 4:
                _validate(value[3], builtins, scope, loop)
            return
        if name == "when":
            if len(value) < 2:
                raise EvalError("when expects a test form")
            _validate(value[1], builtins, scope, _non_tail(loop))
            _validate_seq(value[2:], builtins, scope, loop)
            return
        if name in ("and", "or"):
            # Only the final argument may carry the loop's tail slot.
            _validate_seq(value[1:], builtins, scope, loop)
            return
        if name not in builtins:
            raise EvalError(
                f"unknown function or special form: {name} (in form {dumps(value)})"
            )
        # Builtin arguments are never tail positions (no recursion into
        # builtins, so a recur there would not be a loop tail).
        for arg in value[1:]:
            _validate(arg, builtins, scope, _non_tail(loop))
        return
    if isinstance(value, Symbol):
        if str(value) not in scope:
            raise EvalError(f"undefined symbol: {value}")
        return
    if isinstance(value, list):
        for item in value:
            _validate(item, builtins, scope, _non_tail(loop))
    if isinstance(value, dict):
        # map keys stay literal; values must be valid expressions
        for item in value.values():
            _validate(item, builtins, scope, _non_tail(loop))


def _validate_let(
    form: Form, builtins: dict[str, Builtin], scope: set[str], loop: _WhileCtx | None = None
) -> None:
    args = form[1:]
    if len(args) < 2:
        raise EvalError("let expects a binding vector and at least one body form")
    names = _binding_names(args[0], "let")
    local = set(scope)
    for name, init in zip(names, args[0][1::2], strict=True):
        _validate(init, builtins, local, _non_tail(loop))
        local.add(name)
    # The final body form may be a loop tail (Clojure let).
    _validate_seq(args[1:], builtins, local, loop)


def _validate_while(
    form: Form, builtins: dict[str, Builtin], scope: set[str], loop: _WhileCtx | None
) -> None:
    args = form[1:]
    if len(args) < 2:
        raise EvalError("while expects a binding vector and a test")
    names = _binding_names(args[0], "while")
    local = set(scope)
    for name, init in zip(names, args[0][1::2], strict=True):
        _validate(init, builtins, local, _non_tail(loop))
        local.add(name)
    # The test is not a tail position; recur is rejected inside it.
    _validate(args[1], builtins, local, _non_tail(loop))
    arity = len(names)
    for body_form in args[2:]:
        # Every direct body form is a tail position of this while.
        _validate(body_form, builtins, local, _WhileCtx(arity, True))


def _validate_recur(
    form: Form, builtins: dict[str, Builtin], scope: set[str], loop: _WhileCtx | None
) -> None:
    if loop is None:
        raise EvalError("recur outside while")
    if not loop.tail:
        raise EvalError("recur must be in tail position of while")
    if len(form) - 1 != loop.arity:
        raise EvalError(f"recur expects {loop.arity} arguments, got {len(form) - 1}")
    for arg in form[1:]:
        _validate(arg, builtins, scope, _non_tail(loop))


def _validate_map_like(
    form: Form, builtins: dict[str, Builtin], scope: set[str], loop: _WhileCtx | None
) -> None:
    name = str(form[0])
    args = form[1:]
    if len(args) != 3:
        raise EvalError(f"{name} expects a binding, a body, and a collection")
    binder = args[0]
    if not isinstance(binder, Symbol):
        raise EvalError(f"{name} binding must be a symbol, got {dumps(binder)}")
    local = set(scope)
    local.add(str(binder))
    _validate(args[1], builtins, local, _non_tail(loop))
    _validate(args[2], builtins, scope, _non_tail(loop))


def _validate_reduce(
    form: Form, builtins: dict[str, Builtin], scope: set[str], loop: _WhileCtx | None
) -> None:
    args = form[1:]
    if len(args) != 5:
        raise EvalError("reduce expects accumulator, element, body, init, and collection")
    acc, x = args[0], args[1]
    if not isinstance(acc, Symbol) or not isinstance(x, Symbol):
        raise EvalError("reduce bindings must be symbols")
    local = set(scope)
    local.add(str(acc))
    local.add(str(x))
    _validate(args[2], builtins, local, _non_tail(loop))
    _validate(args[3], builtins, scope, _non_tail(loop))
    _validate(args[4], builtins, scope, _non_tail(loop))


# ── Trigger builtins (pure predicates/functions only) ──


def _self_map(env: dict[str, Any]) -> dict[str, Any]:
    self_map = env.get("self")
    return self_map if isinstance(self_map, dict) else {}


def _my_address(env: dict[str, Any]) -> str | None:
    """The evaluating agent's mail address, derived from ``self``."""
    agent_id = _self_map(env).get("id")
    return address_for_member(agent_id) if agent_id else None


def _builtin_addressed_to_me(env: dict[str, Any], msg: Mail | None) -> bool:
    """My address is in the mail's To list.

    None-safe: the unified member env binds ``msg`` to None for non-mail
    events (phase C), and a non-mail payload is never addressed to me.
    """
    my_address = _my_address(env)
    return my_address is not None and isinstance(msg, Mail) and my_address in msg.to


def _builtin_cc_ed(env: dict[str, Any], msg: Mail | None) -> bool:
    """My address is in the mail's CC list (None-safe, see addressed-to-me?)."""
    my_address = _my_address(env)
    return my_address is not None and isinstance(msg, Mail) and my_address in msg.cc


def _builtin_content_matches(env: dict[str, Any], target: Any, pattern: Any) -> bool:
    """Regex-search a Mail's content or a plain string (e.g. ``result``).

    ``target=None`` (a non-mail event's ``msg``) safely yields False;
    any other non-Mail non-string target is a spec bug and raises.
    """
    if target is None:
        return False
    if isinstance(target, Mail):
        content = target.content
    elif type(target) is str:
        content = target
    else:
        raise EvalError(
            f"content-matches? expects a mail or string, got {type(target).__name__}"
        )
    if not isinstance(pattern, str):
        raise EvalError(
            f"content-matches? pattern must be a string, got {type(pattern).__name__}"
        )
    try:
        return re.search(pattern, content) is not None
    except re.error as e:
        raise EvalError(f"content-matches? invalid regex {pattern!r}: {e}") from e


def _builtin_contains(env: dict[str, Any], coll: Any, item: Any) -> bool:
    if isinstance(coll, str):
        return isinstance(item, str) and item in coll
    if isinstance(coll, (list, tuple)):
        return item in coll
    raise EvalError(f"contains? expects a vector or string, got {type(coll).__name__}")


def _value_equal(a: Any, b: Any) -> bool:
    # Keyword/Symbol/string stay distinct types (``:a`` ≠ ``"a"``).
    if isinstance(a, str) or isinstance(b, str):
        return bool(type(a) is type(b) and a == b)
    return bool(a == b)


def _builtin_eq(env: dict[str, Any], *args: Any) -> bool:
    if len(args) < 2:
        return True
    return all(_value_equal(args[0], arg) for arg in args[1:])


def _builtin_not(env: dict[str, Any], value: Any) -> bool:
    return not value


TRIGGER_BUILTINS: dict[str, Builtin] = {
    "addressed-to-me?": Builtin(_builtin_addressed_to_me, pure=True),
    "cc-ed?": Builtin(_builtin_cc_ed, pure=True),
    "content-matches?": Builtin(_builtin_content_matches, pure=True),
    "contains?": Builtin(_builtin_contains, pure=True),
    "=": Builtin(_builtin_eq, pure=True),
    "not": Builtin(_builtin_not, pure=True),
}


# ── Loop until builtins (phase 7a, pure predicates only) ──


def _loop_state_int(env: dict[str, Any], section: str, key: str) -> int | None:
    """Read an int from ``env[section][key]``; None when absent or not an int."""
    section_map = env.get(section)
    if not isinstance(section_map, dict):
        return None
    value = section_map.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _builtin_iteration_limit_reached(env: dict[str, Any]) -> bool:
    """``state.iteration`` has reached ``limits.max_iterations``."""
    iteration = _loop_state_int(env, "state", "iteration")
    max_iterations = _loop_state_int(env, "limits", "max_iterations")
    if iteration is None or max_iterations is None:
        return False
    return iteration >= max_iterations


def _builtin_error_limit_reached(env: dict[str, Any]) -> bool:
    """``state.error_count`` has reached ``limits.max_consecutive_errors``."""
    error_count = _loop_state_int(env, "state", "error_count")
    max_errors = _loop_state_int(env, "limits", "max_consecutive_errors")
    if error_count is None or max_errors is None:
        return False
    return error_count >= max_errors


#: Loop termination predicates (phase 7a). Pure only; evaluation is
#: side-effect free by construction. The environment provides ``self``,
#: ``state`` (iteration / error_count) and ``limits`` (max_iterations /
#: max_consecutive_errors); missing keys safely yield False, never raise.
LOOP_BUILTINS: dict[str, Builtin] = {
    "iteration-limit-reached?": Builtin(_builtin_iteration_limit_reached, pure=True),
    "error-limit-reached?": Builtin(_builtin_error_limit_reached, pure=True),
    "=": Builtin(_builtin_eq, pure=True),
    "not": Builtin(_builtin_not, pure=True),
}


# ── Stage condition builtins (phase 7c, pure predicates only) ──


def _scratch_map(env: dict[str, Any]) -> dict[str, Any]:
    """The ``scratch`` section of the env; empty map when absent or mistyped."""
    scratch = env.get("scratch")
    return scratch if isinstance(scratch, dict) else {}


def _builtin_critique_failed(env: dict[str, Any]) -> bool:
    """A critique verdict exists in scratch and did not pass."""
    critique = _scratch_map(env).get("critique")
    return isinstance(critique, dict) and critique.get("passed") is False


def _builtin_critique_passed(env: dict[str, Any]) -> bool:
    """A critique verdict exists in scratch and passed."""
    critique = _scratch_map(env).get("critique")
    return isinstance(critique, dict) and critique.get("passed") is True


def _builtin_plan_exists(env: dict[str, Any]) -> bool:
    """Scratch holds a non-empty plan string."""
    plan = _scratch_map(env).get("plan")
    return isinstance(plan, str) and plan != ""


def _builtin_first_iteration(env: dict[str, Any]) -> bool:
    """``state.iteration`` is 0 (the loop's first round)."""
    return _loop_state_int(env, "state", "iteration") == 0


#: Stage ``:if`` condition predicates (phase 7c). Pure only; evaluation is
#: side-effect free by construction. The environment provides ``state``
#: (iteration / error_count) and ``scratch`` (the session's loop
#: scratchpad); missing keys safely yield False, never raise.
STAGE_COND_BUILTINS: dict[str, Builtin] = {
    "critique-failed?": Builtin(_builtin_critique_failed, pure=True),
    "critique-passed?": Builtin(_builtin_critique_passed, pure=True),
    "plan-exists?": Builtin(_builtin_plan_exists, pure=True),
    "first-iteration?": Builtin(_builtin_first_iteration, pure=True),
    "=": Builtin(_builtin_eq, pure=True),
    "not": Builtin(_builtin_not, pure=True),
}


# ── Member action names (phase C unified vocabulary) ──


@dataclass(frozen=True)
class ActionRequest:
    """A side-effecting action recorded during pure evaluation.

    Evaluation of an action builtin performs no side effect; it validates
    its arguments and either appends one of these to a collector
    (on-complete, phase 4b) or returns it as the action form's value
    (script rules, phase P5a). The engine executes the produced requests
    after evaluation completes.
    """

    kind: str  # "spawn" | "emit" | "start" | "stop" | "forward"
    data: dict[str, Any]


ACTION_NAMES = frozenset({"spawn", "emit"})


# ── Script member builtins (phase P5a) ──


def _builtin_get(env: dict[str, Any], target: Any, key: Any, default: Any = None) -> Any:
    """Read ``key`` from a dict payload; missing keys yield ``default``.

    Non-dict targets also yield the default: script rules run on
    arbitrary bus events and must never raise on malformed payloads.
    """
    if not isinstance(target, dict):
        return default
    return target.get(key, default)


#: Script member pure accessors/predicates (phase P5a). Pure only;
#: evaluation is side-effect free by construction. The environment
#: provides ``event`` (the triggering event's payload) and ``self``.
SCRIPT_BUILTINS: dict[str, Builtin] = {
    "get": Builtin(_builtin_get, pure=True),
    "contains?": Builtin(_builtin_contains, pure=True),
    "=": Builtin(_builtin_eq, pure=True),
    "not": Builtin(_builtin_not, pure=True),
}


# ── Arithmetic builtins (phase D: bounded loops need numbers) ──


def _num(x: Any) -> int | float:
    """Coerce to a number; bool and non-numbers are rejected."""
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        raise EvalError(f"expected a number, got {type(x).__name__}")
    return x


def _builtin_add(env: dict[str, Any], *args: Any) -> int | float:
    total: int | float = 0
    for arg in args:
        total += _num(arg)
    return total


def _builtin_mul(env: dict[str, Any], *args: Any) -> int | float:
    total: int | float = 1
    for arg in args:
        total *= _num(arg)
    return total


def _builtin_sub(env: dict[str, Any], a: Any, b: Any | None = None) -> int | float:
    return -_num(a) if b is None else _num(a) - _num(b)


def _builtin_inc(env: dict[str, Any], a: Any) -> int | float:
    return _num(a) + 1


def _builtin_dec(env: dict[str, Any], a: Any) -> int | float:
    return _num(a) - 1


def _builtin_lt(env: dict[str, Any], a: Any, b: Any) -> bool:
    return _num(a) < _num(b)


def _builtin_gt(env: dict[str, Any], a: Any, b: Any) -> bool:
    return _num(a) > _num(b)


def _builtin_le(env: dict[str, Any], a: Any, b: Any) -> bool:
    return _num(a) <= _num(b)


def _builtin_ge(env: dict[str, Any], a: Any, b: Any) -> bool:
    return _num(a) >= _num(b)


#: Phase D arithmetic vocabulary for fuel-bounded loops and data
#: transformation. Pure only; merged into the member registry.
ARITHMETIC_BUILTINS: dict[str, Builtin] = {
    "+": Builtin(_builtin_add, pure=True),
    "-": Builtin(_builtin_sub, pure=True),
    "*": Builtin(_builtin_mul, pure=True),
    "inc": Builtin(_builtin_inc, pure=True),
    "dec": Builtin(_builtin_dec, pure=True),
    "<": Builtin(_builtin_lt, pure=True),
    ">": Builtin(_builtin_gt, pure=True),
    "<=": Builtin(_builtin_le, pure=True),
    ">=": Builtin(_builtin_ge, pure=True),
}


class ScriptHostQueries(Protocol):
    """Host read-only queries closure-injected into the script registry.

    The implementation lives outside this module — ``RegistryQueries`` in
    ``agents/agent_registry.py``, shared by registry-spawned script members
    and Adam — so the module-level registry stays free of host state.
    Query arguments may be anything an expression yields (including None
    for a missing payload key), so implementations must tolerate
    non-string input.
    """

    def is_sleeping(self, agent_id: Any) -> bool:
        """Registered (config known) but not running."""
        ...

    def is_running(self, agent_id: Any) -> bool:
        """Currently in the run table."""
        ...

    def is_lazy(self, agent_id: Any) -> bool:
        """A member that can sleep / be drained: contractor, or its spec has
        no ``:on :boot`` rule (or the spec is unreadable). Phase E."""
        ...

    def start_all_candidates(self) -> list[str]:
        """The boot set: members whose spec declares ``:on :boot``. Phase E."""
        ...


#: Lifecycle action names a script rule may use (phase P5a; P5b adds ``forward``).
SCRIPT_ACTION_NAMES = frozenset({"start", "stop", "start-all", "forward"})

#: The unified action vocabulary (phase C): every rule kind — cognitive or
#: deterministic — may use any of these five actions in its ``:actions``.
MEMBER_ACTION_NAMES = frozenset(ACTION_NAMES | SCRIPT_ACTION_NAMES)


def _script_action_fields(name: str, options: Any, require_agent: bool) -> dict[str, str]:
    """Validate a script action's options map; return the normalized fields.

    ``:reason`` is always a required non-empty string; ``:agent`` is
    required for ``start`` / ``stop`` and rejected for ``start-all``.
    """
    if not isinstance(options, dict):
        raise EvalError(
            f"{name} expects an options map, got {type(options).__name__}"
        )
    fields = {str(key): value for key, value in options.items()}
    unknown = set(fields) - {"agent", "reason"}
    if unknown:
        raise EvalError(f"{name} options: unknown keys {sorted(unknown)}")
    reason = fields.get("reason")
    if type(reason) is not str or not reason:
        raise EvalError(f"{name} :reason must be a non-empty string, got {reason!r}")
    out = {"reason": reason}
    agent = fields.get("agent")
    if require_agent:
        if type(agent) is not str or not agent:
            raise EvalError(f"{name} :agent must be a non-empty string, got {agent!r}")
        out["agent_id"] = agent
    elif agent is not None:
        raise EvalError(f"{name} takes no :agent option")
    return out


def _script_forward_fields(options: Any) -> dict[str, Any]:
    """Validate a ``forward`` action's options map; return the normalized fields.

    ``:to`` is a required non-empty vector of address strings; ``:cc`` is
    an optional address vector; ``:subject`` is an optional string. The
    forwarded mail's content comes from the triggering event at execution
    time — the action data only carries routing.
    """
    if not isinstance(options, dict):
        raise EvalError(
            f"forward expects an options map, got {type(options).__name__}"
        )
    fields = {str(key): value for key, value in options.items()}
    unknown = set(fields) - {"to", "cc", "subject"}
    if unknown:
        raise EvalError(f"forward options: unknown keys {sorted(unknown)}")

    def _addresses(name: str, value: Any) -> list[str]:
        if (
            not isinstance(value, list)
            or not value
            or not all(type(address) is str and address for address in value)
        ):
            raise EvalError(
                f"forward :{name} must be a non-empty vector of address "
                f"strings, got {value!r}"
            )
        return list(value)

    to = _addresses("to", fields.get("to"))
    cc_value = fields.get("cc")
    cc = _addresses("cc", cc_value) if cc_value is not None else []
    subject = fields.get("subject")
    if subject is not None and type(subject) is not str:
        raise EvalError(f"forward :subject must be a string, got {subject!r}")
    return {"to": to, "cc": cc, "subject": subject}


def make_member_builtins(
    host: ScriptHostQueries | None = None,
    collector: list[ActionRequest] | None = None,
) -> dict[str, Builtin]:
    """Build the unified member registry (phase C).

    Replaces the three legacy registries (``make_actions_builtins`` /
    ``make_script_builtins`` and the module-level pure sets) with one:
    the pure predicates/accessors of the trigger and script vocabularies
    are merged, and all five action builtins (``spawn`` / ``emit`` /
    ``start`` / ``stop`` / ``start-all`` / ``forward``) validate their
    arguments and produce :class:`ActionRequest` data without side
    effects.

    How a request reaches the engine depends on the rule kind:

    - deterministic rules collect the *return value* of each action form
      (``collector=None``) — ``(spawn ...)`` evaluates to its request,
      ``(start-all ...)`` to a list of requests;
    - on-complete bodies append every request into ``collector`` when one
      is attached, so ``(when cond (spawn ...) (emit ...))`` records all
      body actions even though ``when`` evaluates to its last body value.

    ``_record`` does both when a collector is given — appends *and*
    returns — so the two paths share one implementation.

    Host predicates (``sleeping?`` / ``running?`` / ``lazy?``) and the
    ``start-all`` candidate set are closure-injected via ``host`` — they
    read registry state, so they are never registered module-level.
    ``start-all`` without a host raises at evaluation time. Action
    arguments may be pure expressions referencing ``event`` / ``msg`` /
    ``result`` (e.g. ``(get event :recipient)``); option-map values are
    evaluated before the builtin sees them.
    """

    def _record(request: ActionRequest) -> ActionRequest:
        if collector is not None:
            collector.append(request)
        return request

    def _spawn(env: dict[str, Any], prototype: Any, options: Any = None) -> ActionRequest:
        if type(prototype) is not str:
            raise EvalError(
                f"spawn prototype must be a string, got {type(prototype).__name__}"
            )
        name: str | None = None
        if options is not None:
            if not isinstance(options, dict):
                raise EvalError(
                    f"spawn options must be a map, got {type(options).__name__}"
                )
            unknown = set(options) - {"name"}
            if unknown:
                raise EvalError(f"spawn options: unknown keys {sorted(unknown)}")
            raw_name = options.get("name")
            if raw_name is not None:
                if type(raw_name) is not str:
                    raise EvalError(
                        f"spawn :name must be a string, got {type(raw_name).__name__}"
                    )
                name = raw_name
        return _record(
            ActionRequest(kind="spawn", data={"prototype": prototype, "name": name})
        )

    def _emit(env: dict[str, Any], content: Any, to: Any = None) -> ActionRequest:
        if type(content) is not str:
            raise EvalError(f"emit content must be a string, got {type(content).__name__}")
        resolved: list[str] = []
        if to is not None:
            if not isinstance(to, (list, tuple)):
                raise EvalError(
                    f"emit recipients must be a vector, got {type(to).__name__}"
                )
            for item in to:
                if not isinstance(item, str):
                    raise EvalError(
                        f"emit recipient must be a string, got {type(item).__name__}"
                    )
                resolved.append(str(item))
        return _record(
            ActionRequest(kind="emit", data={"content": content, "to": resolved})
        )

    def _start(env: dict[str, Any], options: Any) -> ActionRequest:
        fields = _script_action_fields("start", options, require_agent=True)
        return ActionRequest(
            kind="start",
            data={"agent_id": fields["agent_id"], "reason": fields["reason"]},
        )

    def _stop(env: dict[str, Any], options: Any) -> ActionRequest:
        fields = _script_action_fields("stop", options, require_agent=True)
        return ActionRequest(
            kind="stop",
            data={"agent_id": fields["agent_id"], "reason": fields["reason"]},
        )

    def _start_all(env: dict[str, Any], options: Any) -> list[ActionRequest]:
        fields = _script_action_fields("start-all", options, require_agent=False)
        if host is None:
            raise EvalError("start-all unavailable: no host queries injected")
        return [
            ActionRequest(kind="start", data={"agent_id": agent_id, "reason": fields["reason"]})
            for agent_id in host.start_all_candidates()
        ]

    def _forward(env: dict[str, Any], options: Any) -> ActionRequest:
        fields = _script_forward_fields(options)
        return ActionRequest(
            kind="forward",
            data={"to": fields["to"], "cc": fields["cc"], "subject": fields["subject"]},
        )

    builtins: dict[str, Builtin] = {
        **TRIGGER_BUILTINS,
        **SCRIPT_BUILTINS,
        **ARITHMETIC_BUILTINS,
        "spawn": Builtin(_spawn, pure=False),
        "emit": Builtin(_emit, pure=False),
        "start": Builtin(_start, pure=False),
        "stop": Builtin(_stop, pure=False),
        "start-all": Builtin(_start_all, pure=False),
        "forward": Builtin(_forward, pure=False),
    }
    if host is not None:
        builtins["sleeping?"] = Builtin(lambda env, aid: host.is_sleeping(aid), pure=True)
        builtins["running?"] = Builtin(lambda env, aid: host.is_running(aid), pure=True)
        builtins["lazy?"] = Builtin(lambda env, aid: host.is_lazy(aid), pure=True)
    return builtins


def validate_member_action(
    form: Any, *, pure: dict[str, Builtin], scope: set[str]
) -> None:
    """Statically validate one member action form (phase C unified gate).

    The action position of any rule — cognitive or deterministic — is
    either a single action form (``(spawn ...)`` / ``(emit ...)`` /
    ``(start ...)`` / ``(stop ...)`` / ``(start-all ...)`` /
    ``(forward ...)``), a ``(when cond action ...)`` wrapper, or a phase D
    loop form (``while`` / ``map``) whose action-bearing positions hold
    action forms. The purity invariant is unchanged (dsl-agents.md phase D):
    conditions, tests, binding initializers and action arguments are pure
    expressions validated against ``pure`` (the pure subset of the member
    registry) under ``scope``; only the action-carrying positions may name
    an action.
    """
    if not isinstance(form, Form) or not form:
        raise EvalError(
            f"member action must be an action form or (when ...), got {dumps(form)}"
        )
    _validate_member_action_expr(form, pure, set(scope))


def _validate_member_action_expr(
    form: Any, pure: dict[str, Builtin], scope: set[str]
) -> None:
    """Validate one member action expression (a single action or a wrapper).

    ``when`` wraps action forms under a pure condition; phase D ``while`` /
    ``map`` loop over action forms. Every other head must be an action name
    whose arguments are pure expressions.
    """
    if not isinstance(form, Form) or not form:
        raise EvalError(f"action must be an action form, got {dumps(form)}")
    head = form[0]
    if not isinstance(head, Symbol):
        raise EvalError(f"action head must be a symbol, got {dumps(head)}")
    name = str(head)
    if name in MEMBER_ACTION_NAMES:
        for arg in form[1:]:
            validate_form(arg, pure, set(scope))
        return
    if name == "when":
        if len(form) < 3:
            raise EvalError("when expects a condition and at least one action")
        validate_form(form[1], pure, set(scope))
        for action in form[2:]:
            _validate_member_action_expr(action, pure, scope)
        return
    if name == "while":
        if len(form) < 3:
            raise EvalError("while expects a binding vector and a test")
        names = _binding_names(form[1], "while")
        local = set(scope)
        for binding, init in zip(names, form[1][1::2], strict=True):
            validate_form(init, pure, set(local))
            local.add(binding)
        validate_form(form[2], pure, set(local))
        for body in form[3:]:
            _validate_loop_body_action(body, pure, local)
        return
    if name == "map":
        if len(form) != 4:
            raise EvalError("map expects a binding, a body, and a collection")
        binder = form[1]
        if not isinstance(binder, Symbol):
            raise EvalError(f"map binding must be a symbol, got {dumps(binder)}")
        local = set(scope)
        local.add(str(binder))
        _validate_member_action_expr(form[2], pure, local)
        validate_form(form[3], pure, set(scope))
        return
    raise EvalError(
        f"unknown action: {dumps(head)} "
        f"(allowed: {sorted(MEMBER_ACTION_NAMES)}, when, while, map)"
    )


def _validate_loop_body_action(
    form: Any, pure: dict[str, Builtin], scope: set[str]
) -> None:
    """A ``while`` body position: an action expression or the loop's ``recur``."""
    if (
        isinstance(form, Form)
        and form
        and isinstance(form[0], Symbol)
        and str(form[0]) == "recur"
    ):
        for arg in form[1:]:
            validate_form(arg, pure, set(scope))
        return
    _validate_member_action_expr(form, pure, scope)
