"""Static validation for the agent behavior DSL (split out of ``eval.py``).

:func:`validate_form` walks a parsed EDN form without executing it:
every form head must be a whitelisted special form, a registered
:class:`Builtin`, or a name in ``scope`` (a user function defined by
``defn`` — ADR 0021); every symbol must resolve in ``scope`` (extended
by ``let`` / loop bindings and ``defn`` / ``fn`` parameters), and
special-form shapes must be well-formed — raising :class:`EvalError` on
the first violation. The ``while`` / ``recur`` tail-position rules are
tracked through :class:`_WhileCtx`: ``recur`` is only legal in a
``while`` body's tail position (``if`` branches, trailing
``when``/``let`` bodies, and the final ``and``/``or`` argument inherit
tail position), and never crosses a function boundary — ``fn`` /
``defn`` bodies are validated with no enclosing loop.

``defn`` scope threading is sequential (define-before-use): a body is
validated form by form, and each ``defn`` adds its name to the scope of
the *following* sibling forms — referencing a function before its
``defn`` is rejected even though evaluation (by-reference env capture)
would resolve it.

Action-position validation is host business (it names the host's action
vocabulary) and lives with the host — for tlon that is
``tlon/agents/vocabulary.py`` (ADR 0018 修订).

The ``_binding_names`` / ``_params`` helpers are shared with the
evaluator kernel (``tlosure/eval.py``), which uses them for ``let`` /
``while`` binding vectors and ``defn`` / ``fn`` parameter vectors at
evaluation time, and with host-side action validators.
"""

from dataclasses import dataclass
from typing import Any

from tlosure.builtins import Builtin, EvalError
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
    """Validate a body: only its final form may carry the loop's tail slot.

    Threads ``defn`` names forward (define-before-use): each ``defn``
    adds its name to the scope the *following* sibling forms see.
    """
    local = set(scope)
    n = len(forms)
    for i, form in enumerate(forms):
        sub = loop if (loop is not None and i == n - 1) else _non_tail(loop)
        if _is_defn(form):
            local.add(_validate_defn(form, builtins, local, sub))
            continue
        _validate(form, builtins, local, sub)


def _is_defn(value: Any) -> bool:
    return (
        isinstance(value, Form)
        and bool(value)
        and isinstance(value[0], Symbol)
        and str(value[0]) == "defn"
    )


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


def _params(params: Any, what: str) -> tuple[list[str], str | None]:
    """Parse a function parameter vector: ``[a b]`` or ``[a b & rest]``.

    ``what`` names the construct in error messages ("defn" / "fn").
    Returns the fixed parameter names and the variadic collector name
    (``None`` when absent). ``&`` must sit exactly in the penultimate
    position, followed by one symbol.
    """
    if isinstance(params, Form) or not isinstance(params, list):
        raise EvalError(f"{what} parameters must be a vector, got {dumps(params)}")
    items = list(params)
    rest: str | None = None
    if Symbol("&") in items:
        if items.count(Symbol("&")) > 1 or len(items) < 2 or items[-2] != Symbol("&"):
            raise EvalError(f"{what} parameters: & must precede the final parameter")
        if not isinstance(items[-1], Symbol):
            raise EvalError(f"{what} parameters: & must be followed by a symbol")
        rest = str(items[-1])
        items = items[:-2]
    names: list[str] = []
    for item in items:
        if not isinstance(item, Symbol):
            raise EvalError(f"{what} parameter name must be a symbol, got {dumps(item)}")
        names.append(str(item))
    return names, rest


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
            # A Form head is a function-valued expression applied to the
            # arguments (e.g. ``((fn [x] (inc x)) 5)``); anything else
            # stays a shape error.
            if not isinstance(head, Form):
                raise EvalError(f"form head must be a symbol, got {dumps(head)}")
            _validate(head, builtins, scope, _non_tail(loop))
            for arg in value[1:]:
                _validate(arg, builtins, scope, _non_tail(loop))
            return
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
        if name == "defn":
            _validate_defn(value, builtins, scope, loop)
            return
        if name == "fn":
            _validate_fn(value, builtins, scope, loop)
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
        if name in scope:
            # A name bound in scope heads a user function call (the
            # evaluator resolves env before builtins, mirroring this);
            # arguments are validated as ordinary non-tail expressions.
            for arg in value[1:]:
                _validate(arg, builtins, scope, _non_tail(loop))
            return
        if name not in builtins:
            raise EvalError(f"unknown function or special form: {name} (in form {dumps(value)})")
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


def _validate_defn(
    form: Form, builtins: dict[str, Builtin], scope: set[str], loop: _WhileCtx | None = None
) -> str:
    """Validate ``(defn name [params] body...)``; return the defined name.

    The body sees the definition scope plus the parameters, the variadic
    collector, and the function's own name (recursion). It is validated
    with no enclosing loop: ``recur`` never crosses a function boundary.
    """
    args = form[1:]
    if len(args) < 3:
        raise EvalError("defn expects a name, a parameter vector, and at least one body form")
    name = args[0]
    if not isinstance(name, Symbol):
        raise EvalError(f"defn name must be a symbol, got {dumps(name)}")
    params, rest = _params(args[1], "defn")
    local = set(scope) | set(params) | {str(name)}
    if rest is not None:
        local.add(rest)
    _validate_seq(args[2:], builtins, local, None)
    return str(name)


def _validate_fn(
    form: Form, builtins: dict[str, Builtin], scope: set[str], loop: _WhileCtx | None = None
) -> None:
    """Validate ``(fn [params] body...)``: an anonymous closure value.

    The body sees the definition scope plus the parameters (and the
    variadic collector); ``recur`` inside is rejected (no enclosing
    loop crosses a function boundary).
    """
    args = form[1:]
    if len(args) < 2:
        raise EvalError("fn expects a parameter vector and at least one body form")
    params, rest = _params(args[0], "fn")
    local = set(scope) | set(params)
    if rest is not None:
        local.add(rest)
    _validate_seq(args[1:], builtins, local, None)


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
        # defn names thread forward like any other body (_validate_seq),
        # mirroring the evaluator's shared per-iteration env dict.
        if _is_defn(body_form):
            local.add(_validate_defn(body_form, builtins, local, _WhileCtx(arity, True)))
            continue
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
