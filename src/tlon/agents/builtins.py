"""Builtin vocabulary for the agent behavior DSL (split out of ``eval.py``).

Everything a form head may name beyond the special forms lives here:

- the shared contract types the vocabulary and the evaluator kernel
  (``agents/eval.py``) are both written against — the :class:`EvalError`
  family, :class:`Builtin` (one registry entry: ``fn`` + purity marker),
  and :class:`ActionRequest` (the side-effect *data* an action builtin
  produces; the engine executes the requests after evaluation
  completes — "the DSL describes, the engine executes");
- the pure module-level registries — :data:`TRIGGER_BUILTINS` (trigger
  expressions), :data:`LOOP_BUILTINS` (phase 7a loop termination),
  :data:`STAGE_COND_BUILTINS` (phase 7c stage gating over loop state and
  the session scratchpad), :data:`SCRIPT_BUILTINS` (phase P5a script
  rules), :data:`ARITHMETIC_BUILTINS` (phase D) — pure predicates and
  functions only, so evaluation against any of them is side-effect free
  by construction;
- :func:`make_member_builtins` (phase C): the unified member registry —
  the merged pure vocabularies plus the action builtins (``spawn`` /
  ``emit`` / ``start`` / ``stop`` / ``start-all`` / ``forward``,
  ``pure=False``). Host predicates that read registry state
  (``sleeping?`` / ``running?`` / ``lazy?``), the ``member-roster``
  social roster (issue ) and the ``start-all`` candidate set are
  closure-injected via :class:`ScriptHostQueries`, never registered
  module-level.

This module is the leaf of the evaluator pair: it imports nothing from
``agents/eval.py``; the kernel imports the contract types from here, so
there is no import cycle.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from tlon.agents.edn import dumps
from tlon.organization.mail.mail import Mail, address_for_member


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


def _builtin_from(env: dict[str, Any], msg: Mail | None, address: Any) -> bool:
    """The mail's sender equals ``address`` (None-safe, see addressed-to-me?).

    Mail field access for trigger gates — ``get`` reads dicts only, so
    sender matching gets its own pure predicate (issue : Adam's
    guardian-inbox rule excludes postmaster senders to never answer a
    bounce). A non-string ``address`` never matches.
    """
    return (
        isinstance(msg, Mail)
        and isinstance(address, str)
        and msg.from_address == address
    )


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
    "from?": Builtin(_builtin_from, pure=True),
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
    """``state.error_count`` (consecutive think failures; a successful think resets it to 0) has reached ``limits.max_consecutive_errors``."""
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


def _builtin_completing(env: dict[str, Any]) -> bool:
    """The round's pending response would end the session if dispatched.

    True for a completion signal — ``sendEmail`` with ``is_complete=true``
    or ``silence`` — with no conflicting tool calls (act's conflict rule).
    """
    round_map = env.get("round")
    return isinstance(round_map, dict) and round_map.get("completing") is True


#: Stage ``:if`` condition predicates (phase 7c). Pure only; evaluation is
#: side-effect free by construction. The environment provides ``state``
#: (iteration / error_count), ``scratch`` (the session's loop scratchpad)
#: and ``round`` (``completing`` — whether the response currently on the
#: round's chain would end the session); missing keys safely yield False,
#: never raise.
STAGE_COND_BUILTINS: dict[str, Builtin] = {
    "critique-failed?": Builtin(_builtin_critique_failed, pure=True),
    "critique-passed?": Builtin(_builtin_critique_passed, pure=True),
    "plan-exists?": Builtin(_builtin_plan_exists, pure=True),
    "first-iteration?": Builtin(_builtin_first_iteration, pure=True),
    "completing?": Builtin(_builtin_completing, pure=True),
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


def _builtin_str(env: dict[str, Any], *args: Any) -> str:
    """Concatenate arguments into one string (issue ).

    String arguments are inserted verbatim; anything else is rendered in
    its EDN write form (``:foo`` → ``":foo"``, ``[1 2]`` → ``"[1 2]"``),
    so a payload value can never smash the surrounding text unawares.
    Pure and total; the fuel counter already bounds the argument count.
    """
    return "".join(arg if type(arg) is str else dumps(arg) for arg in args)


#: Script member pure accessors/predicates (phase P5a). Pure only;
#: evaluation is side-effect free by construction. The environment
#: provides ``event`` (the triggering event's payload) and ``self``.
SCRIPT_BUILTINS: dict[str, Builtin] = {
    "get": Builtin(_builtin_get, pure=True),
    "str": Builtin(_builtin_str, pure=True),
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
        """A member that can sleep / be drained: its spec has no ``:on :boot``
        rule (or the spec is unreadable). Unified member model (ADR 0006):
        laziness is spec-derived, never a birth marker."""
        ...

    def start_all_candidates(self) -> list[str]:
        """The boot set: members whose spec declares ``:on :boot``. Phase E."""
        ...

    def member_roster(self) -> str:
        """The social roster (issue ): one pre-formatted line per
        registered member — name, id, prototype and its description.

        Formatting lives in the implementation, not the DSL: the DSL never
        iterates, iteration is a builtin. Social public information, open
        to every member (the same injected host serves Adam and
        registry-spawned members)."""
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

    Host predicates (``sleeping?`` / ``running?`` / ``lazy?``), the
    ``member-roster`` social roster and the ``start-all`` candidate set
    are closure-injected via ``host`` — they read registry state, so they
    are never registered module-level. ``start-all`` without a host raises
    at evaluation time; ``member-roster`` without a host is simply absent
    from the registry (static validation rejects it), like ``sleeping?``.
    Action arguments may be pure expressions referencing ``event`` / ``msg`` /
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
        builtins["member-roster"] = Builtin(lambda env: host.member_roster(), pure=True)
    return builtins
