"""Static validation for the agent behavior DSL (split out of ``eval.py``).

:func:`validate_form` walks a parsed EDN form without executing it:
every form head must be a whitelisted special form or a registered
:class:`Builtin`, every symbol must resolve in ``scope`` (extended by
``let`` / loop bindings), and special-form shapes must be well-formed —
raising :class:`EvalError` on the first violation. The ``while`` /
``recur`` tail-position rules are tracked through :class:`_WhileCtx`:
``recur`` is only legal in a ``while`` body's tail position (``if``
branches, trailing ``when``/``let`` bodies, and the final ``and``/``or``
argument inherit tail position).

:func:`validate_member_action` is the phase C unified gate for a rule's
action position: action forms (see
:data:`tlosure.builtins.MEMBER_ACTION_NAMES`) under ``when`` /
``while`` / ``map`` wrappers, whose conditions, tests, binding
initializers and arguments are pure expressions validated against the
pure subset of the member registry.

The ``_binding_names`` helper is shared with the evaluator kernel
(``tlosure/eval.py``), which uses it for ``let`` / ``while`` binding
vectors at evaluation time.
"""

from dataclasses import dataclass
from typing import Any

from tlosure.builtins import MEMBER_ACTION_NAMES, Builtin, EvalError
from tlosure.edn import Form, Symbol, dumps


@dataclass(frozen=True)
class _WhileCtx:
    """Nearest enclosing ``while`` context threaded through static validation.

    ``arity`` is the loop's binding count (the ``recur`` argument count it
    accepts); ``tail`` marks whether the current position is a tail
    position of that loop (where ``recur`` is legal).
    """

    arity: int
    tail: bool


def validate_form(form: Any, builtins: dict[str, Builtin], scope: set[str]) -> None:
    """Statically check an expression without executing it.

    Walks the form verifying that every form head is a whitelisted
    special form or a registered builtin, every symbol resolves in
    ``scope`` (extended by ``let`` bindings), and special-form shapes are
    well-formed. Raises :class:`EvalError` on the first violation.
    """
    _validate(form, builtins, set(scope))


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


def validate_member_action(
    form: Any, *, pure: dict[str, Builtin], scope: set[str]
) -> None:
    """Statically validate one member action form (phase C unified gate).

    The action position of any rule — cognitive or deterministic — is
    either a single action form (``(spawn ...)`` / ``(emit ...)`` /
    ``(start ...)`` / ``(stop ...)`` / ``(start-all ...)`` /
    ``(forward ...)``), a ``(when cond action ...)`` wrapper, or a phase D
    loop form (``while`` / ``map``) whose action-bearing positions hold
    action forms. The purity invariant is unchanged (dsl.md phase D):
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
