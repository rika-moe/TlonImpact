"""Restricted evaluator for the agent behavior DSL (L1, phase 3a).

Evaluates parsed EDN Forms (``agents/edn.py``) under a deliberately
non-Turing-complete semantics (design decisions 2/3 in
``docs/design/dsl-agents.md``):

- special forms are a closed whitelist: ``if`` / ``when`` / ``let`` /
  ``and`` / ``or`` — no ``def``, no ``lambda``, no ``loop``/``recur``,
  no recursion (there is no way to name a function), no ``match`` (3b+);
- every other form head must name a registered :class:`Builtin`;
- evaluation is metered: each evaluation step consumes one unit of
  *fuel* (:class:`FuelExhaustedError` when it runs out) and form nesting
  is capped (:class:`EvalDepthError`), so evaluation always terminates;
- builtins carry a purity marker. The trigger registry
  (:data:`TRIGGER_BUILTINS`) registers pure predicates/functions only —
  side-effecting actions (``spawn`` / ``emit``) are never registered
  here, so trigger evaluation is side-effect free by construction.

On-complete action expressions (phase 4b) keep the same rule: evaluation
never performs side effects. The action builtins ``spawn`` / ``emit``
(registered only in :func:`make_actions_builtins`, marked ``pure=False``)
validate their arguments and record an :class:`ActionRequest` into a
collector; the engine executes the collected requests asynchronously
after evaluation completes ("the DSL describes, the engine executes").
Static validation distinguishes two purity positions
(:func:`validate_action_form`): a ``when`` condition must be pure, an
action position must be an action form with pure arguments.

Environment values are opaque to expressions: there is no attribute or
method access syntax, so a ``Message`` in ``env`` can only be inspected
through builtins. ``env`` also provides ``self`` (``{"id": ..., "name":
...}``) which builtins read to resolve "me".
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tlon.agents.edn import Form, Symbol, dumps
from tlon.organization.messaging.message import Message

DEFAULT_FUEL = 1000
DEFAULT_MAX_DEPTH = 100

SPECIAL_FORMS = frozenset({"if", "when", "let", "and", "or"})


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
    return _Evaluator(builtins, fuel, max_depth).eval(form, dict(env), 0)


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
        # nil / bool / number / string / Keyword / map: self-evaluating
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
        raise EvalError(f"unknown special form: {name}")

    def _eval_bindings(self, bindings: Any, env: dict[str, Any], depth: int) -> dict[str, Any]:
        if isinstance(bindings, Form) or not isinstance(bindings, list):
            raise EvalError(f"let bindings must be a vector, got {dumps(bindings)}")
        if len(bindings) % 2 != 0:
            raise EvalError("let bindings must contain an even number of forms")
        local = dict(env)
        for i in range(0, len(bindings), 2):
            name = bindings[i]
            if not isinstance(name, Symbol):
                raise EvalError(f"let binding name must be a symbol, got {dumps(name)}")
            local[str(name)] = self.eval(bindings[i + 1], local, depth + 1)
        return local

    def _eval_body(self, body: list[Any], env: dict[str, Any], depth: int) -> Any:
        result: Any = None
        for form in body:
            result = self.eval(form, env, depth + 1)
        return result


def _validate(value: Any, builtins: dict[str, Builtin], scope: set[str]) -> None:
    if isinstance(value, Form):
        if not value:
            raise EvalError("empty form () is not a valid expression")
        head = value[0]
        if not isinstance(head, Symbol):
            raise EvalError(f"form head must be a symbol, got {dumps(head)}")
        name = str(head)
        if name == "let":
            _validate_let(value, builtins, scope)
            return
        if name in SPECIAL_FORMS:
            for arg in value[1:]:
                _validate(arg, builtins, scope)
            return
        if name not in builtins:
            raise EvalError(
                f"unknown function or special form: {name} (in form {dumps(value)})"
            )
        for arg in value[1:]:
            _validate(arg, builtins, scope)
        return
    if isinstance(value, Symbol):
        if str(value) not in scope:
            raise EvalError(f"undefined symbol: {value}")
        return
    if isinstance(value, list):
        for item in value:
            _validate(item, builtins, scope)


def _validate_let(form: Form, builtins: dict[str, Builtin], scope: set[str]) -> None:
    args = form[1:]
    if len(args) < 2:
        raise EvalError("let expects a binding vector and at least one body form")
    bindings = args[0]
    if isinstance(bindings, Form) or not isinstance(bindings, list):
        raise EvalError(f"let bindings must be a vector, got {dumps(bindings)}")
    if len(bindings) % 2 != 0:
        raise EvalError("let bindings must contain an even number of forms")
    local = set(scope)
    for i in range(0, len(bindings), 2):
        name = bindings[i]
        if not isinstance(name, Symbol):
            raise EvalError(f"let binding name must be a symbol, got {dumps(name)}")
        _validate(bindings[i + 1], builtins, local)
        local.add(str(name))
    for body_form in args[1:]:
        _validate(body_form, builtins, local)


# ── Trigger builtins (pure predicates/functions only) ──


def _self_map(env: dict[str, Any]) -> dict[str, Any]:
    self_map = env.get("self")
    return self_map if isinstance(self_map, dict) else {}


def _builtin_mentioned(env: dict[str, Any], msg: Message) -> bool:
    me = _self_map(env)
    return me.get("id") in msg.mentions or me.get("name") in msg.mentions


def _builtin_addressed_to_me(env: dict[str, Any], msg: Message) -> bool:
    """Mentions are present and include me.

    Distinct from proactive broadcast: a message with mentions that do
    not include me is addressed to someone else (room crosstalk).
    """
    return bool(msg.mentions) and _builtin_mentioned(env, msg)


def _builtin_content_matches(env: dict[str, Any], target: Any, pattern: Any) -> bool:
    """Regex-search a Message's content or a plain string (e.g. ``result``)."""
    if isinstance(target, Message):
        content = target.content
    elif type(target) is str:
        content = target
    else:
        raise EvalError(
            f"content-matches? expects a message or string, got {type(target).__name__}"
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
        return type(a) is type(b) and a == b
    return a == b


def _builtin_eq(env: dict[str, Any], *args: Any) -> bool:
    if len(args) < 2:
        return True
    return all(_value_equal(args[0], arg) for arg in args[1:])


def _builtin_not(env: dict[str, Any], value: Any) -> bool:
    return not value


TRIGGER_BUILTINS: dict[str, Builtin] = {
    "mentioned?": Builtin(_builtin_mentioned, pure=True),
    "addressed-to-me?": Builtin(_builtin_addressed_to_me, pure=True),
    "content-matches?": Builtin(_builtin_content_matches, pure=True),
    "contains?": Builtin(_builtin_contains, pure=True),
    "=": Builtin(_builtin_eq, pure=True),
    "not": Builtin(_builtin_not, pure=True),
}


# ── On-complete action builtins (phase 4b) ──


@dataclass(frozen=True)
class ActionRequest:
    """A side-effecting action recorded during pure evaluation.

    Evaluation of an action builtin performs no side effect; it validates
    its arguments and appends one of these to a collector. The engine
    (Agent) executes the collected requests after evaluation completes.
    """

    kind: str  # "spawn" | "emit"
    data: dict[str, Any]


ACTION_NAMES = frozenset({"spawn", "emit"})


def make_actions_builtins(collector: list[ActionRequest] | None) -> dict[str, Builtin]:
    """Build the on-complete registry: pure predicates + action builtins.

    ``spawn`` / ``emit`` are marked ``pure=False`` and never touch the
    outside world — they validate arguments and record an
    :class:`ActionRequest` into ``collector``. ``collector=None`` yields
    a registry suitable for static validation (requests are discarded);
    evaluation with it collects nothing.
    """

    def _record(request: ActionRequest) -> None:
        if collector is not None:
            collector.append(request)

    def _spawn(env: dict[str, Any], prototype: Any, options: Any = None) -> None:
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
        _record(ActionRequest(kind="spawn", data={"prototype": prototype, "name": name}))

    def _emit(env: dict[str, Any], content: Any, mentions: Any = None) -> None:
        if type(content) is not str:
            raise EvalError(f"emit content must be a string, got {type(content).__name__}")
        resolved: list[str] = []
        if mentions is not None:
            if not isinstance(mentions, (list, tuple)):
                raise EvalError(
                    f"emit mentions must be a vector, got {type(mentions).__name__}"
                )
            for item in mentions:
                if not isinstance(item, str):
                    raise EvalError(
                        f"emit mention must be a string, got {type(item).__name__}"
                    )
                resolved.append(str(item))
        _record(ActionRequest(kind="emit", data={"content": content, "mentions": resolved}))

    return {
        **TRIGGER_BUILTINS,
        "spawn": Builtin(_spawn, pure=False),
        "emit": Builtin(_emit, pure=False),
    }


#: Validation-only on-complete registry (no collector attached).
ACTIONS_BUILTINS: dict[str, Builtin] = make_actions_builtins(None)


def validate_action_form(form: Any, scope: set[str]) -> None:
    """Statically validate one on-complete action form (no execution).

    The two purity positions are checked with different registries:

    - top level is either a single action form (``(spawn ...)`` /
      ``(emit ...)``) or ``(when cond action ...)``;
    - the ``when`` condition validates against :data:`TRIGGER_BUILTINS`
      (pure only — actions in the condition are rejected);
    - each action position must be an action form whose arguments are
      pure expressions. Nested ``when`` is not supported.
    """
    if not isinstance(form, Form) or not form:
        raise EvalError(
            f"on-complete action must be an action form or (when ...), got {dumps(form)}"
        )
    head = form[0]
    if isinstance(head, Symbol) and str(head) == "when":
        if len(form) < 3:
            raise EvalError("when expects a condition and at least one action")
        validate_form(form[1], TRIGGER_BUILTINS, set(scope))
        for action in form[2:]:
            _validate_action_expr(action, scope)
        return
    _validate_action_expr(form, scope)


def _validate_action_expr(form: Any, scope: set[str]) -> None:
    if not isinstance(form, Form) or not form:
        raise EvalError(f"action must be a (spawn ...)/(emit ...) form, got {dumps(form)}")
    head = form[0]
    if not isinstance(head, Symbol) or str(head) not in ACTION_NAMES:
        raise EvalError(
            f"unknown action: {dumps(head)} (allowed: {sorted(ACTION_NAMES)})"
        )
    for arg in form[1:]:
        validate_form(arg, TRIGGER_BUILTINS, set(scope))
