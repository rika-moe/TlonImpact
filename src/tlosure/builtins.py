"""Contract types and general vocabulary for the language core.

Everything here is host-agnostic (ADR 0018 修订 — the core naming test:
a construct belongs in tlosure only if it can be explained without
naming a host business concept):

- the shared contract types the vocabulary and the evaluator kernel
  (``tlosure/eval.py``) are both written against — the :class:`EvalError`
  family, :class:`Builtin` (one registry entry: ``fn`` + purity marker),
  and :class:`ActionRequest` (the side-effect *data* an action builtin
  produces; the host engine executes the requests after evaluation
  completes — "the DSL describes, the engine executes");
- the general vocabulary: pure, total functions any host composes into
  its own registries — :data:`CORE_BUILTINS` (``=`` / ``not`` /
  ``contains?`` / ``get`` / ``str``) and :data:`ARITHMETIC_BUILTINS`
  (``+`` / ``-`` / ``*`` / ``inc`` / ``dec`` / comparisons). Pure only,
  so evaluation against either registry is side-effect free by
  construction.

Domain vocabulary — host-specific predicates, action families, host
query ports — lives with the host, never here.

This module is the leaf of the evaluator pair: it imports nothing from
``tlosure/eval.py``; the kernel imports the contract types from here, so
there is no import cycle.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tlosure.edn import dumps


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
    side effects. Hosts that reserve an evaluation context for pure
    functions enforce that at registration time, via this marker.
    """

    fn: Callable[..., Any]
    pure: bool = True


@dataclass(frozen=True)
class ActionRequest:
    """A side-effecting action recorded during pure evaluation.

    Evaluation of an action builtin performs no side effect; it validates
    its arguments and either appends one of these to a collector (when the
    host attached one) or returns it as the action form's value. The host
    engine executes the produced requests after evaluation completes.
    """

    kind: str  # the action name (the host's action vocabulary)
    data: dict[str, Any]


# ── General vocabulary (pure, total functions) ──


def builtin_contains(env: dict[str, Any], coll: Any, item: Any) -> bool:
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


def builtin_eq(env: dict[str, Any], *args: Any) -> bool:
    if len(args) < 2:
        return True
    return all(_value_equal(args[0], arg) for arg in args[1:])


def builtin_not(env: dict[str, Any], value: Any) -> bool:
    return not value


def builtin_get(env: dict[str, Any], target: Any, key: Any, default: Any = None) -> Any:
    """Read ``key`` from a dict payload; missing keys yield ``default``.

    Non-dict targets also yield the default: the accessor is total and
    must never raise on malformed payloads.
    """
    if not isinstance(target, dict):
        return default
    return target.get(key, default)


def builtin_str(env: dict[str, Any], *args: Any) -> str:
    """Concatenate arguments into one string (issue ).

    String arguments are inserted verbatim; anything else is rendered in
    its EDN write form (``:foo`` → ``":foo"``, ``[1 2]`` → ``"[1 2]"``),
    so a payload value can never smash the surrounding text unawares.
    Pure and total; the fuel counter already bounds the argument count.
    """
    return "".join(arg if type(arg) is str else dumps(arg) for arg in args)


#: General-purpose pure vocabulary: host-agnostic building blocks any
#: host registry may include verbatim. Pure only; evaluation against it
#: is side-effect free by construction.
CORE_BUILTINS: dict[str, Builtin] = {
    "=": Builtin(builtin_eq, pure=True),
    "not": Builtin(builtin_not, pure=True),
    "contains?": Builtin(builtin_contains, pure=True),
    "get": Builtin(builtin_get, pure=True),
    "str": Builtin(builtin_str, pure=True),
}


# ── Arithmetic builtins (bounded loops need numbers) ──


def _num(x: Any) -> int | float:
    """Coerce to a number; bool and non-numbers are rejected."""
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        raise EvalError(f"expected a number, got {type(x).__name__}")
    return x


def builtin_add(env: dict[str, Any], *args: Any) -> int | float:
    total: int | float = 0
    for arg in args:
        total += _num(arg)
    return total


def builtin_mul(env: dict[str, Any], *args: Any) -> int | float:
    total: int | float = 1
    for arg in args:
        total *= _num(arg)
    return total


def builtin_sub(env: dict[str, Any], a: Any, b: Any | None = None) -> int | float:
    return -_num(a) if b is None else _num(a) - _num(b)


def builtin_inc(env: dict[str, Any], a: Any) -> int | float:
    return _num(a) + 1


def builtin_dec(env: dict[str, Any], a: Any) -> int | float:
    return _num(a) - 1


def builtin_lt(env: dict[str, Any], a: Any, b: Any) -> bool:
    return _num(a) < _num(b)


def builtin_gt(env: dict[str, Any], a: Any, b: Any) -> bool:
    return _num(a) > _num(b)


def builtin_le(env: dict[str, Any], a: Any, b: Any) -> bool:
    return _num(a) <= _num(b)


def builtin_ge(env: dict[str, Any], a: Any, b: Any) -> bool:
    return _num(a) >= _num(b)


#: Arithmetic vocabulary for fuel-bounded loops and data transformation.
#: Pure only; hosts merge it into their registries as needed.
ARITHMETIC_BUILTINS: dict[str, Builtin] = {
    "+": Builtin(builtin_add, pure=True),
    "-": Builtin(builtin_sub, pure=True),
    "*": Builtin(builtin_mul, pure=True),
    "inc": Builtin(builtin_inc, pure=True),
    "dec": Builtin(builtin_dec, pure=True),
    "<": Builtin(builtin_lt, pure=True),
    ">": Builtin(builtin_gt, pure=True),
    "<=": Builtin(builtin_le, pure=True),
    ">=": Builtin(builtin_ge, pure=True),
}
