"""Contract types and general vocabulary for the language core.

Everything here is host-agnostic (ADR 0018 修订 — the core naming test:
a construct belongs in tlosure only if it can be explained without
naming a host business concept):

- the shared contract types the vocabulary and the evaluator kernel
  (``tlosure/eval.py``) are both written against — the :class:`EvalError`
  family, :class:`Builtin` (one registry entry: a purity marker plus
  either a host ``fn`` or a data-defined :class:`Closure`),
  :class:`Closure` (the user function value ``defn`` / ``fn`` produce,
  ADR 0021), and :class:`BuiltinCall` (a suspended host call
  whose result an asynchronous host supplies back to the expression);
- the general vocabulary: pure, total functions any host composes into
  its own registries — :data:`CORE_BUILTINS` (``=`` / ``not`` /
  ``contains?`` / ``get`` / ``str`` / ``split`` / ``join`` /
  ``substring`` / immutable collection updates) and :data:`ARITHMETIC_BUILTINS`
  (``+`` / ``-`` / ``*`` / ``/`` / ``inc`` / ``dec`` / comparisons). Pure only,
  so evaluation against either registry is side-effect free by
  construction.

Domain vocabulary — host-specific predicates, action families, host
query ports — lives with the host, never here.

This module is the leaf of the evaluator pair: it imports nothing from
``tlosure/eval.py``; the kernel imports the contract types from here, so
there is no import cycle.
"""

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tlosure.edn import Keyword, dumps


class EvalError(Exception):
    """Any evaluation or static-validation failure of a DSL expression."""


class FuelExhaustedError(EvalError):
    """The evaluation step budget ran out.

    Carries the ``budget`` the evaluation was launched with, so hosts can
    log/meter exhaustion structurally instead of parsing the message.
    """

    def __init__(self, message: str, *, budget: int):
        super().__init__(message)
        self.budget = budget


class EvalDepthError(EvalError):
    """The form nesting depth cap was exceeded."""


@dataclass(frozen=True)
class Closure:
    """A user function value (ADR 0021) — what ``defn`` / ``fn`` evaluate to.

    A language-core value, host-agnostic like the rest of this module:
    ``params`` are the fixed parameter names, ``rest`` the variadic
    collector name (``[a b & rest]``), ``body`` the unevaluated forms,
    and ``env`` the definition-time environment captured **by reference**
    (recursion depends on it: ``defn`` writes the closure into the same
    dict object, so the body's self-reference resolves). ``name`` is the
    ``defn`` name, for error messages only.
    """

    params: list[str]
    rest: str | None
    body: list[Any]
    env: dict[str, Any]
    name: str | None = None


@dataclass(frozen=True)
class Builtin:
    """A registry entry with a purity marker — two implementation shapes.

    Host shape (``fn`` set): ``fn`` receives ``(env, *evaluated_args)``;
    its internals are **unmetered** — a host builtin's own computation
    is the host's resource domain, not the language's.

    Closure-backed shape (``closure`` set): a data-defined word — the
    entry carries a :class:`Closure` instead of host code, and the
    evaluator applies it like any closure application: the body
    evaluates through the kernel, so **fuel draw-down and the depth cap
    count into the caller's budget** (the substantive semantic
    difference from the host shape). The body sees exactly the closure's
    captured ``env`` plus its parameter bindings — registering an empty
    env means the body sees its arguments and nothing else (zero
    injection); words inside the body resolve through the *caller's*
    registry like any other form head.

    Exactly one of ``fn`` / ``closure`` must be set. ``pure`` marks
    deterministic computation. External reads suspend like writes and
    are excluded from pure condition vocabularies.
    """

    fn: Callable[..., Any] | None = None
    pure: bool = True
    closure: Closure | None = None

    def __post_init__(self) -> None:
        if (self.fn is None) == (self.closure is None):
            raise ValueError("Builtin needs exactly one of fn / closure")

    @staticmethod
    def from_closure(closure: Closure, *, pure: bool = True) -> "Builtin":
        """A closure-backed (data-defined) registry entry."""
        return Builtin(pure=pure, closure=closure)


@dataclass(frozen=True)
class BuiltinCall:
    """A suspended impure host call, before its function has been invoked.

    Hosts may return a recorded result without invoking the function. The
    environment and registry entry are runtime objects; durable journals
    record the name, positional arguments and result instead.
    """

    name: str
    builtin: Builtin
    args: list[Any]
    env: dict[str, Any]
    depth: int = 0

    def invoke(self) -> Any:
        """Invoke the host function to validate arguments and produce its data."""
        fn = self.builtin.fn
        if fn is None:
            raise EvalError("closure-backed words must run through the evaluator")
        return fn(self.env, *self.args)


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
    """Concatenate arguments into one string.

    String arguments are inserted verbatim; anything else is rendered in
    its EDN write form (``:foo`` → ``":foo"``, ``[1 2]`` → ``"[1 2]"``),
    so a payload value can never smash the surrounding text unawares.
    Pure and total; the fuel counter already bounds the argument count.
    """
    return "".join(arg if type(arg) is str else dumps(arg) for arg in args)


def _str_arg(name: str, value: Any) -> str:
    """Coerce to a plain string; everything else is a spec bug."""
    if type(value) is not str:
        raise EvalError(f"{name} expects a string, got {type(value).__name__}")
    return value


def builtin_split(env: dict[str, Any], s: Any, sep: Any) -> list[str]:
    """Split a string on a separator into a vector of strings.

    An empty separator is rejected (Python raises ``ValueError`` there);
    a non-string argument is a spec bug and raises.
    """
    text = _str_arg("split", s)
    separator = _str_arg("split", sep)
    if not separator:
        raise EvalError("split separator must be non-empty")
    return text.split(separator)


def builtin_join(env: dict[str, Any], sep: Any, coll: Any) -> str:
    """Join a vector of strings with a separator into one string.

    The collection must be a vector of plain strings — a non-string item
    is a spec bug (use ``str`` first to render EDN values) and raises.
    """
    separator = _str_arg("join", sep)
    if not isinstance(coll, list):
        raise EvalError(f"join expects a vector, got {type(coll).__name__}")
    return separator.join(_str_arg("join", item) for item in coll)


def builtin_substring(env: dict[str, Any], s: Any, start: Any, end: Any = None) -> str:
    """``s[start:end]`` — Python slice semantics, total by construction.

    Out-of-range bounds clamp, negative indices count from the end; only
    non-int bounds (bool included) raise.
    """
    text = _str_arg("substring", s)
    for name, bound in (("start", start), ("end", end)):
        if bound is not None and (not isinstance(bound, int) or isinstance(bound, bool)):
            raise EvalError(f"substring {name} must be an int, got {type(bound).__name__}")
    return text[start:end]


def builtin_count(env: dict[str, Any], coll: Any) -> int:
    """Item count of a vector / map / string; ``nil`` counts as 0.

    Clojure semantics (ADR 0011): ``(count nil)`` is 0, a map counts its
    entries, a string its characters. Any other type is a spec bug and
    raises.
    """
    if coll is None:
        return 0
    if isinstance(coll, (list, dict, str)):
        return len(coll)
    raise EvalError(f"count expects a vector, map or string, got {type(coll).__name__}")


def _put_map_entry(
    mapping: dict[Any, Any], key_types: dict[Any, type], key: Any, value: Any
) -> None:
    """Reject cross-type equal keys before Python dict silently erases one."""
    if key in key_types and key_types[key] is not type(key):
        raise EvalError("map keys collide across EDN types")
    key_types[key] = type(key)
    mapping[key] = value


def builtin_assoc(env: dict[str, Any], target: Any, *pairs: Any) -> dict[Any, Any] | list[Any]:
    """Return a map/vector copy with key-value pairs applied in order.

    Vector indices must already exist, or equal its length to append.
    Maps use literal EDN keys; no input collection is mutated.
    """
    if len(pairs) % 2:
        raise EvalError("assoc expects key-value pairs")
    if isinstance(target, dict):
        mapping = dict(target)
        key_types = {key: type(key) for key in mapping}
        for key, value in zip(pairs[::2], pairs[1::2], strict=True):
            try:
                _put_map_entry(mapping, key_types, key, value)
            except TypeError as error:
                raise EvalError("assoc map key must be hashable") from error
        return mapping
    if type(target) is list:
        vector = list(target)
        for key, value in zip(pairs[::2], pairs[1::2], strict=True):
            if type(key) is not int or not 0 <= key <= len(vector):
                raise EvalError("assoc vector index must be an integer within [0, count]")
            if key == len(vector):
                vector.append(value)
            else:
                vector[key] = value
        return vector
    raise EvalError("assoc expects a map or vector")


def builtin_dissoc(env: dict[str, Any], target: Any, *keys: Any) -> dict[Any, Any]:
    """Return a map copy without the supplied keys; missing keys are harmless."""
    if not isinstance(target, dict):
        raise EvalError("dissoc expects a map")
    result = dict(target)
    for key in keys:
        try:
            hash(key)
            result.pop(key, None)
        except TypeError as error:
            raise EvalError("dissoc map key must be hashable") from error
    return result


def builtin_conj(env: dict[str, Any], target: Any, *items: Any) -> dict[Any, Any] | list[Any]:
    """Append to a vector, or add two-element entry vectors to a map copy."""
    if type(target) is list:
        return [*target, *items]
    if isinstance(target, dict):
        result = dict(target)
        key_types = {key: type(key) for key in result}
        for item in items:
            if type(item) is not list or len(item) != 2:
                raise EvalError("conj map entries must be two-element vectors")
            try:
                _put_map_entry(result, key_types, item[0], item[1])
            except TypeError as error:
                raise EvalError("conj map key must be hashable") from error
        return result
    raise EvalError("conj expects a map or vector")


def builtin_keys(env: dict[str, Any], target: Any) -> list[Any]:
    """Return map keys in insertion order."""
    if not isinstance(target, dict):
        raise EvalError("keys expects a map")
    return list(target)


def builtin_vals(env: dict[str, Any], target: Any) -> list[Any]:
    """Return map values in insertion order, matching ``keys``."""
    if not isinstance(target, dict):
        raise EvalError("vals expects a map")
    return list(target.values())


def builtin_integer(env: dict[str, Any], value: Any) -> bool:
    """Test for an integer value, excluding booleans."""
    return type(value) is int


def builtin_assert(env: dict[str, Any], condition: Any, message: str) -> Any:
    """Reject invalid data without introducing a host effect."""
    if not condition:
        raise EvalError(str(message))
    return condition


def builtin_json_encode(env: dict[str, Any], value: Any) -> str:
    """Encode strict JSON data, accepting EDN keywords only as object keys."""
    active: set[int] = set()

    def normalize(item: Any, depth: int) -> Any:
        if depth > 100:
            raise EvalError("JSON nesting exceeds 100")
        if item is None or type(item) in (str, bool, int):
            return item
        if type(item) is float and math.isfinite(item):
            return item
        if type(item) not in (list, dict):
            raise EvalError(f"not a JSON value: {type(item).__name__}")
        identity = id(item)
        if identity in active:
            raise EvalError("cyclic JSON value")
        active.add(identity)
        try:
            if type(item) is list:
                return [normalize(child, depth + 1) for child in item]
            result: dict[str, Any] = {}
            for key, child in item.items():
                if type(key) is Keyword:
                    key = str(key)
                if type(key) is not str:
                    raise EvalError("JSON object keys must be strings or keywords")
                if key in result:
                    raise EvalError(f"duplicate normalized JSON key: {key}")
                result[key] = normalize(child, depth + 1)
            return result
        finally:
            active.remove(identity)

    return json.dumps(
        normalize(value, 0), ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )


#: General-purpose pure vocabulary: host-agnostic building blocks any
#: host registry may include verbatim. Pure only; evaluation against it
#: is side-effect free by construction.
CORE_BUILTINS: dict[str, Builtin] = {
    "assert": Builtin(builtin_assert, pure=True),
    "integer?": Builtin(builtin_integer, pure=True),
    "json-encode": Builtin(builtin_json_encode, pure=True),
    "assoc": Builtin(builtin_assoc, pure=True),
    "dissoc": Builtin(builtin_dissoc, pure=True),
    "conj": Builtin(builtin_conj, pure=True),
    "keys": Builtin(builtin_keys, pure=True),
    "vals": Builtin(builtin_vals, pure=True),
    "=": Builtin(builtin_eq, pure=True),
    "not": Builtin(builtin_not, pure=True),
    "contains?": Builtin(builtin_contains, pure=True),
    "get": Builtin(builtin_get, pure=True),
    "str": Builtin(builtin_str, pure=True),
    "split": Builtin(builtin_split, pure=True),
    "join": Builtin(builtin_join, pure=True),
    "substring": Builtin(builtin_substring, pure=True),
    "count": Builtin(builtin_count, pure=True),
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


def builtin_div(env: dict[str, Any], a: Any, b: Any, *rest: Any) -> float:
    """Left-fold float division: ``(/ a b c)`` = ``a / b / c``.

    Always float division (no ratio type in the language); a zero divisor
    is a spec bug and raises.
    """
    result = _num(a)
    for divisor in (b, *rest):
        divisor = _num(divisor)
        if divisor == 0:
            raise EvalError("division by zero")
        result /= divisor
    return result


def builtin_sub(env: dict[str, Any], a: Any, b: Any | None = None) -> int | float:
    return -_num(a) if b is None else _num(a) - _num(b)


def builtin_quot(env: dict[str, Any], a: Any, b: Any) -> int:
    """Integer quotient truncated toward zero, without float precision loss."""
    if type(a) is not int or type(b) is not int:
        raise EvalError("quot expects integers")
    if b == 0:
        raise EvalError("division by zero")
    quotient = abs(a) // abs(b)
    return -quotient if (a < 0) != (b < 0) else quotient


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
    "/": Builtin(builtin_div, pure=True),
    "quot": Builtin(builtin_quot, pure=True),
    "inc": Builtin(builtin_inc, pure=True),
    "dec": Builtin(builtin_dec, pure=True),
    "<": Builtin(builtin_lt, pure=True),
    ">": Builtin(builtin_gt, pure=True),
    "<=": Builtin(builtin_le, pure=True),
    ">=": Builtin(builtin_ge, pure=True),
}
