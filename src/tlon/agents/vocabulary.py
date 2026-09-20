"""tlon's DSL domain vocabulary — the host layer over tlosure (ADR 0018 修订).

The language core (``tlosure``) provides the contract types
(:class:`EvalError` family, :class:`Builtin`, :class:`ActionRequest`),
the evaluator kernel, static validation (:func:`tlosure.validate.validate_form`)
and the general vocabulary (``=`` / ``not`` / ``contains?`` / ``get`` /
``str`` / arithmetic). Everything a tlon member's spec may name beyond
that lives here:

- the mail predicates (``addressed-to-me?`` / ``cc-ed?`` / ``from?`` /
  ``content-matches?``), reading the ``msg`` mail object and the
  host-injected ``self.address``;
- the loop termination predicates (phase 7a) over ``state`` / ``limits``
  and the stage ``:if`` condition predicates (phase 7c) over ``state`` /
  ``scratch`` / ``round``;
- the pure module-level registries — :data:`TRIGGER_BUILTINS` (trigger
  expressions), :data:`LOOP_BUILTINS`, :data:`STAGE_COND_BUILTINS`,
  :data:`SCRIPT_BUILTINS` — pure predicates and functions only, so
  evaluation against any of them is side-effect free by construction;
- the action family (``spawn`` / ``emit`` / ``start`` / ``stop`` /
  ``start-all`` / ``forward``, ``pure=False``) and
  :func:`make_member_builtins` (phase C): the unified member registry —
  the merged pure vocabularies plus the action builtins. Host predicates
  that read registry state (``sleeping?`` / ``running?`` / ``lazy?``),
  the ``member-roster`` social roster (issue ) and the ``start-all``
  candidate set are closure-injected via :class:`ScriptHostQueries`,
  never registered module-level;
- :func:`validate_member_action`, the phase C unified static gate for a
  rule's action position.
"""

import re
from typing import Any, Protocol

from tlon.organization.mail.mail import Mail
from tlosure.builtins import (
    ARITHMETIC_BUILTINS,
    ActionRequest,
    Builtin,
    EvalError,
    builtin_contains,
    builtin_eq,
    builtin_get,
    builtin_not,
    builtin_str,
)
from tlosure.edn import Form, Symbol, dumps
from tlosure.validate import _binding_names, validate_form

# ── Trigger builtins (pure predicates/functions only) ──


def _self_map(env: dict[str, Any]) -> dict[str, Any]:
    self_map = env.get("self")
    return self_map if isinstance(self_map, dict) else {}


def _my_address(env: dict[str, Any]) -> str | None:
    """The evaluating agent's mail address, injected by the host in ``self``."""
    address = _self_map(env).get("address")
    return address if type(address) is str and address else None


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
    """Regex-search a mail's content or a plain string (e.g. ``result``).

    ``target=None`` (a non-mail event's ``msg``) safely yields False;
    any other non-mail non-string target is a spec bug and raises.
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


TRIGGER_BUILTINS: dict[str, Builtin] = {
    "addressed-to-me?": Builtin(_builtin_addressed_to_me, pure=True),
    "cc-ed?": Builtin(_builtin_cc_ed, pure=True),
    "from?": Builtin(_builtin_from, pure=True),
    "content-matches?": Builtin(_builtin_content_matches, pure=True),
    "contains?": Builtin(builtin_contains, pure=True),
    "=": Builtin(builtin_eq, pure=True),
    "not": Builtin(builtin_not, pure=True),
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
    "=": Builtin(builtin_eq, pure=True),
    "not": Builtin(builtin_not, pure=True),
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
    "=": Builtin(builtin_eq, pure=True),
    "not": Builtin(builtin_not, pure=True),
}


# ── Member action names (phase C unified vocabulary) ──

ACTION_NAMES = frozenset({"spawn", "emit"})


# ── Script member builtins (phase P5a) ──


#: Script member pure accessors/predicates (phase P5a). Pure only;
#: evaluation is side-effect free by construction. The environment
#: provides ``event`` (the triggering event's payload) and ``self``.
SCRIPT_BUILTINS: dict[str, Builtin] = {
    "get": Builtin(builtin_get, pure=True),
    "str": Builtin(builtin_str, pure=True),
    "contains?": Builtin(builtin_contains, pure=True),
    "=": Builtin(builtin_eq, pure=True),
    "not": Builtin(builtin_not, pure=True),
}


class ScriptHostQueries(Protocol):
    """Host read-only queries closure-injected into the script registry.

    The implementation lives outside this module — ``RegistryQueries`` in
    ``tlon.agents/agent_registry.py``, shared by registry-spawned script members
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


# ── Action-position static validation (phase C unified gate) ──


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
