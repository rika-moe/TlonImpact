"""The Bridge API — tlon's host vocabulary over the tlosure language core.

Design: ``docs/design/bridge-api.md`` (ADR 0020). The bridge between the
Python mechanism layer and tlosure policy data is four port kinds; this
module is their single source of truth:

- **Context registries** — which vocabulary each evaluation context may
  name: :data:`TRIGGER_BUILTINS` (rule ``:when`` gates),
  :data:`LOOP_BUILTINS` (``:loop`` ``:until``), :data:`STAGE_COND_BUILTINS`
  (stage ``:if``) and :data:`SCRIPT_BUILTINS` (pure accessors merged into
  the member registry). All pure, so evaluation against any of them is
  side-effect free by construction; each registry's comment states its
  environment contract (the symbols the engine binds for that context).
- **Query ports** (host → DSL, read-only) — :class:`ScriptHostQueries`, a
  protocol of host read-only queries closure-injected by
  :func:`make_member_builtins` (``sleeping?`` / ``running?`` / ``lazy?`` /
  ``member-roster`` / the ``start-all`` candidate set). They read registry
  state, so they are never registered module-level; the implementation
  (``RegistryQueries`` in ``tlon.agents/agent_registry.py``) is injected at
  assembly time.
- **Effect ports** (DSL → host, effects-as-data) — the action family
  (``spawn`` / ``emit`` / ``start`` / ``stop`` / ``start-all`` /
  ``forward``, ``pure=False``, names in :data:`MEMBER_ACTION_NAMES`):
  each validates its arguments and produces :class:`ActionRequest` data
  without side effects; the engine executes the requests after evaluation
  ends (``MemberRuntime._execute_action``). :func:`make_member_builtins`
  builds the unified member registry — the merged pure vocabularies plus
  the action builtins plus any host-injected queries.
- **Static gate** — :func:`validate_member_actions`, the load-time check
  for a rule's action forms (ADR 0021): the syntax-level purity
  partition is abandoned (a function body can carry actions, so "where
  actions may occur" is no longer statically enumerable); what remains
  is full-registry validation — unregistered vocabulary and undefined
  symbols are still rejected at load — plus sequential ``defn``
  threading across the form sequence (define-before-use), mirroring the
  runtime's shared env dict. The collector (on-complete) and
  return-value collection (deterministic rules) are the actual
  action-convergence mechanism.

Environment contracts: typed, with a single construction point — see
the ``*Env`` TypedDicts and builders in the "Environment contracts"
section below. Rule evaluation binds ``{msg, event, self}``
(:data:`RULE_SCOPE`; ``MemberRuntime._env``), on-complete bodies add
``result`` (:data:`ACTION_SCOPE`; ``on_complete``), ``:until`` binds
``{self, state, limits}`` and stage ``:if`` binds
``{state, scratch, round}`` (both ``loop_driver``); the latter two
expose no *symbols* — their builtins read the env directly, so load-time
validation passes an empty scope.

Bridge versioning discipline (ADR 0020): vocabulary only grows, semantics
only tighten — spec files are on-disk data, so a breaking change to a
bridge word corrupts every stored behavior file. New words declare which
context registries they belong to; no word is visible everywhere.

The language core (``tlosure``) provides the contract types
(:class:`EvalError` family, :class:`Builtin`, :class:`ActionRequest`),
the evaluator kernel, static validation (:func:`tlosure.validate.validate_form`)
and the general vocabulary (``=`` / ``not`` / ``contains?`` / ``get`` /
``str`` / arithmetic).
"""

import re
from typing import Any, Protocol, TypedDict

from tlon.organization.mail.mail import Mail, address_for_member
from tlosure.builtins import (
    ARITHMETIC_BUILTINS,
    CORE_BUILTINS,
    ActionRequest,
    Builtin,
    EvalError,
    builtin_contains,
    builtin_count,
    builtin_eq,
    builtin_get,
    builtin_join,
    builtin_not,
    builtin_split,
    builtin_str,
    builtin_substring,
)
from tlosure.edn import Form
from tlosure.validate import _is_defn, _validate_defn, validate_form

# ── Environment contracts (the data each context may see) ──
#
# Every evaluation context binds a fixed set of symbols with a fixed
# shape; these TypedDicts are those shapes and the builders below are the
# single construction point (the engine call sites delegate here).
# Load-time validation mirrors them: RULE_SCOPE / ACTION_SCOPE are the
# symbol scopes of member_spec.validate_member_spec; the loop and
# stage-cond contexts expose no symbols at all — their builtins read the
# env directly — so validation passes an empty scope for them.


class SelfEnv(TypedDict):
    """The ``self`` binding: the evaluating member's identity.

    ``address`` is pre-derived at construction so DSL builtins read data,
    never call back into tlon (ADR 0018).
    """

    id: str
    name: str
    address: str


class RuleEnv(TypedDict):
    """Rule evaluation context (``:when`` gates, deterministic ``:actions``).

    ``msg`` is the payload's mail, or None for non-mail events (every mail
    predicate is None-safe); ``event`` is the raw event payload — a dict
    whose shape is not fully controlled, so builtins default safely on
    malformed input.
    """

    msg: Mail | None
    event: Any
    self: SelfEnv


class OnCompleteEnv(TypedDict):
    """Cognitive-rule completion actions (``on_complete``).

    ``msg`` was the session's trigger mail; ADR 0028 retired the trigger
    mail, so it is always None on this path (the env shape is kept — the
    vocabulary contract is unchanged). ``result`` is the session's final
    reply text. ``event`` is *not* bound on this path: ACTION_SCOPE is the
    union of the two action evaluation paths (deterministic actions run
    under RuleEnv, completion actions under this one).
    """

    msg: Mail | None
    result: str
    self: SelfEnv


class LoopStateEnv(TypedDict):
    """The ``state`` binding: live loop counters."""

    iteration: int
    error_count: int
    token_usage: int


class LoopLimitsEnv(TypedDict):
    """The ``limits`` binding: the loop's declared caps."""

    max_iterations: int
    max_consecutive_errors: int
    max_tokens: int


class LoopEnv(TypedDict):
    """``:loop`` ``:until`` context (``loop_driver``)."""

    self: SelfEnv
    state: LoopStateEnv
    limits: LoopLimitsEnv


class RoundEnv(TypedDict):
    """The ``round`` binding: facts about the round being gated."""

    completing: bool


class StageCondEnv(TypedDict):
    """Stage ``:if`` context (``loop_driver``): loop state, the session's
    loop scratchpad, and the round's completion flag."""

    state: LoopStateEnv
    scratch: dict[str, Any]
    round: RoundEnv


#: Load-time validation scope for ``:when`` gates (mirrors RuleEnv).
RULE_SCOPE: frozenset[str] = frozenset({"msg", "event", "self"})

#: Load-time validation scope for ``:actions`` — the union of RuleEnv
#: (deterministic rules) and OnCompleteEnv (cognitive completion).
ACTION_SCOPE: frozenset[str] = frozenset({"msg", "event", "result", "self"})


def self_env(member_id: str, name: str) -> SelfEnv:
    """The ``self`` binding for any context."""
    return {"id": member_id, "name": name, "address": address_for_member(member_id)}


def rule_env(payload: Any, *, member_id: str, name: str) -> RuleEnv:
    """The rule evaluation env (``MemberRuntime._env`` delegates here)."""
    mail = payload.get("mail") if isinstance(payload, dict) else None
    return {"msg": mail, "event": payload, "self": self_env(member_id, name)}


def on_complete_env(msg: Mail | None, result: str, *, member_id: str, name: str) -> OnCompleteEnv:
    """The completion-actions env (``on_complete`` delegates here)."""
    return {"msg": msg, "result": result, "self": self_env(member_id, name)}


def loop_until_env(
    *,
    member_id: str,
    name: str,
    iteration: int,
    error_count: int,
    token_usage: int,
    max_iterations: int,
    max_consecutive_errors: int,
    max_tokens: int,
) -> LoopEnv:
    """The ``:until`` env (``loop_driver`` delegates here)."""
    return {
        "self": self_env(member_id, name),
        "state": {
            "iteration": iteration,
            "error_count": error_count,
            "token_usage": token_usage,
        },
        "limits": {
            "max_iterations": max_iterations,
            "max_consecutive_errors": max_consecutive_errors,
            "max_tokens": max_tokens,
        },
    }


def stage_cond_env(
    *,
    iteration: int,
    error_count: int,
    token_usage: int,
    scratch: dict[str, Any],
    completing: bool,
) -> StageCondEnv:
    """The stage ``:if`` env (``loop_driver`` delegates here)."""
    return {
        "state": {
            "iteration": iteration,
            "error_count": error_count,
            "token_usage": token_usage,
        },
        "scratch": scratch,
        "round": {"completing": completing},
    }


# ── Context registries (pure vocabulary, one per evaluation context) ──
#
# Every registry below is pure functions only: evaluation against any of
# them is side-effect free by construction. A word absent from a context's
# registry is rejected at load-time validation — "the author misspoke" is
# a static error, never a runtime surprise.


# ── Trigger context: rule ``:when`` gates ──
# Environment contract: :class:`RuleEnv`, built by :func:`rule_env`;
# ``msg`` is None for non-mail events and every mail predicate is
# None-safe.


def _self_map(env: dict[str, Any]) -> dict[str, Any]:
    self_map = env.get("self")
    return self_map if isinstance(self_map, dict) else {}


def _my_address(env: dict[str, Any]) -> str | None:
    """The evaluating agent's mail address, injected by the host in ``self``."""
    address = _self_map(env).get("address")
    return address if type(address) is str and address else None


def _builtin_addressed_to_me(env: dict[str, Any], msg: Mail | None) -> bool:
    """My address is in the mail's To list.

    None-safe: the member env binds ``msg`` to None for non-mail events,
    and a non-mail payload is never addressed to me.
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
    sender matching gets its own pure predicate (Adam's guardian-inbox
    rule excludes postmaster senders to never answer a bounce). A
    non-string ``address`` never matches.
    """
    return isinstance(msg, Mail) and isinstance(address, str) and msg.from_address == address


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
        raise EvalError(f"content-matches? expects a mail or string, got {type(target).__name__}")
    if not isinstance(pattern, str):
        raise EvalError(f"content-matches? pattern must be a string, got {type(pattern).__name__}")
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


# ── Loop context: ``:loop`` ``:until`` termination ──
# Environment contract: :class:`LoopEnv`, built by :func:`loop_until_env`.


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


def _builtin_token_limit_reached(env: dict[str, Any]) -> bool:
    """``state.token_usage`` (session-cumulative stage LLM tokens, ADR 0023) has reached ``limits.max_tokens``."""
    token_usage = _loop_state_int(env, "state", "token_usage")
    max_tokens = _loop_state_int(env, "limits", "max_tokens")
    if token_usage is None or max_tokens is None:
        return False
    return token_usage >= max_tokens


#: Loop termination predicates. Pure only; evaluation is side-effect free
#: by construction. Missing keys safely yield False, never raise.
LOOP_BUILTINS: dict[str, Builtin] = {
    "iteration-limit-reached?": Builtin(_builtin_iteration_limit_reached, pure=True),
    "error-limit-reached?": Builtin(_builtin_error_limit_reached, pure=True),
    "token-limit-reached?": Builtin(_builtin_token_limit_reached, pure=True),
    "=": Builtin(builtin_eq, pure=True),
    "not": Builtin(builtin_not, pure=True),
}


# ── Stage-cond context: stage ``:if`` conditions ──
# Environment contract: :class:`StageCondEnv`, built by
# :func:`stage_cond_env`.


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


#: Stage ``:if`` condition predicates. Pure only; evaluation is
#: side-effect free by construction. Missing keys safely yield False,
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


# ── Script pure accessors ──
# Environment contract: :class:`RuleEnv` (deterministic rules) /
# :class:`OnCompleteEnv` (cognitive completion actions). Merged into the
# unified member registry by :func:`make_member_builtins`.


#: Pure accessors available to every rule kind. Pure only; evaluation is
#: side-effect free by construction. Kept a superset of the capability
#: admission registry (``CAPABILITY_PURE_BUILTINS`` = CORE + ARITHMETIC,
#: ``agents/capabilities/spec.py``) so every word a capability ``:impl``
#: may compose resolves in the caller's registry at runtime — the
#: vocabulary bridge's runtime contract (a regression test in
#: ``tests/agents/capabilities/test_bridge.py`` pins the subset relation).
SCRIPT_BUILTINS: dict[str, Builtin] = {
    "get": Builtin(builtin_get, pure=True),
    "str": Builtin(builtin_str, pure=True),
    "split": Builtin(builtin_split, pure=True),
    "join": Builtin(builtin_join, pure=True),
    "substring": Builtin(builtin_substring, pure=True),
    "contains?": Builtin(builtin_contains, pure=True),
    "count": Builtin(builtin_count, pure=True),
    "=": Builtin(builtin_eq, pure=True),
    "not": Builtin(builtin_not, pure=True),
}


# ── Policy context: society policy hooks (ADR 0022) ──
# The fifth evaluation context, the first pull-typed one: the host calls
# the policy file's hooks (``tilt`` / ``solvent?`` / ``full-member?``) at
# fixed evaluation points (spawn / staging proposal / assembly) instead
# of reacting to an event. Environment contract: no symbols at all —
# hooks are functions the host *applies* with positional arguments whose
# shapes are the TypedDicts below, materialized by the host before
# evaluation (social data collection is async IO, impossible inside a
# pure builtin). Mechanism collects raw facts, policy judges.


class LineageMemberStats(TypedDict):
    """One member's contribution to its bloodline's raw stats."""

    id: str
    endorsements_received: int


class LineageStats(TypedDict):
    """One bloodline's raw social facts (the ``tilt`` / ``solvent?`` input).

    ``total_tokens`` and the per-member endorsement counts are raw
    aggregates; the value / efficiency formulas derived from them live in
    the policy file. The bloodline key is the prototype name
    (``base/ledger.py`` ``collect_lineage_ledgers``).
    """

    name: str
    total_tokens: int
    members: list[LineageMemberStats]


class PolicyMemberInfo(TypedDict):
    """One member's social facts (the ``full-member?`` input)."""

    id: str
    spawned_by: str | None
    endorsements_received: int


#: Society-policy vocabulary (ADR 0022): the pure language subset — core
#: accessors and arithmetic, plus the evaluator's special forms. No
#: action words, no host query callbacks: policy hooks receive
#: materialized data and return a verdict, so evaluation against this
#: registry is side-effect free by construction.
POLICY_BUILTINS: dict[str, Builtin] = {**CORE_BUILTINS, **ARITHMETIC_BUILTINS}

#: The hook names the host looks up in a policy file's evaluated env.
#: Versioning discipline (ADR 0020): this set only grows.
POLICY_HOOK_NAMES = frozenset({"tilt", "solvent?", "full-member?"})


# ── Query ports (host → DSL, read-only) ──


class ScriptHostQueries(Protocol):
    """Host read-only queries closure-injected into the member registry.

    The implementation lives outside this module — ``RegistryQueries`` in
    ``tlon.agents/agent_registry.py``, shared by registry-spawned members
    and Adam — so the module-level registries stay free of host state.
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
        """A member that can sleep / be drained: no ``:on :boot`` rule.

        Unified member model (ADR 0006): laziness is spec-derived (a
        missing or unreadable spec also counts), never a birth marker."""
        ...

    def start_all_candidates(self) -> list[str]:
        """The boot set: members whose spec declares ``:on :boot``."""
        ...

    def member_roster(self) -> str:
        """The addressing yellow page: one pre-formatted line per member.

        Each line is ``name (id) — id@tlon.internal`` (ADR 0024 决策 3:
        id / name / address, no identity content). Formatting lives
        in the implementation, not the DSL: the DSL never iterates,
        iteration is a builtin. Public routing information, open to every
        member (the same injected host serves Adam and registry-spawned
        members); sleeping members stay listed, Adam itself is not
        registered and so not listed."""
        ...


# ── Effect ports (DSL → host, effects-as-data) ──
#
# Action builtins validate their arguments during pure evaluation and
# produce ActionRequest data; execution happens in the engine after
# evaluation ends (``MemberRuntime._execute_action``), so a spec can never
# reach past the engine's quotas and trust gates.


#: The unified action vocabulary: every rule kind — cognitive or
#: deterministic — may use any of these actions in its ``:actions``.
MEMBER_ACTION_NAMES = frozenset({"spawn", "emit", "start", "stop", "start-all", "forward"})


def _script_action_fields(name: str, options: Any, require_agent: bool) -> dict[str, str]:
    """Validate a script action's options map; return the normalized fields.

    ``:reason`` is always a required non-empty string; ``:agent`` is
    required for ``start`` / ``stop`` and rejected for ``start-all``.
    """
    if not isinstance(options, dict):
        raise EvalError(f"{name} expects an options map, got {type(options).__name__}")
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
        raise EvalError(f"forward expects an options map, got {type(options).__name__}")
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
                f"forward :{name} must be a non-empty vector of address strings, got {value!r}"
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
    """Build the unified member registry.

    The pure predicates/accessors of the trigger and script vocabularies
    are merged with the arithmetic vocabulary, plus all six action
    builtins (``spawn`` / ``emit`` / ``start`` / ``stop`` / ``start-all``
    / ``forward``) which validate their arguments and produce
    :class:`ActionRequest` data without side effects.

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
    ``member-roster`` addressing yellow page and the ``start-all``
    candidate set
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
            raise EvalError(f"spawn prototype must be a string, got {type(prototype).__name__}")
        name: str | None = None
        if options is not None:
            if not isinstance(options, dict):
                raise EvalError(f"spawn options must be a map, got {type(options).__name__}")
            unknown = set(options) - {"name"}
            if unknown:
                raise EvalError(f"spawn options: unknown keys {sorted(unknown)}")
            raw_name = options.get("name")
            if raw_name is not None:
                if type(raw_name) is not str:
                    raise EvalError(f"spawn :name must be a string, got {type(raw_name).__name__}")
                name = raw_name
        return _record(ActionRequest(kind="spawn", data={"prototype": prototype, "name": name}))

    def _emit(env: dict[str, Any], content: Any, to: Any = None) -> ActionRequest:
        if type(content) is not str:
            raise EvalError(f"emit content must be a string, got {type(content).__name__}")
        resolved: list[str] = []
        if to is not None:
            if not isinstance(to, (list, tuple)):
                raise EvalError(f"emit recipients must be a vector, got {type(to).__name__}")
            for item in to:
                if not isinstance(item, str):
                    raise EvalError(f"emit recipient must be a string, got {type(item).__name__}")
                resolved.append(str(item))
        return _record(ActionRequest(kind="emit", data={"content": content, "to": resolved}))

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


# ── Static gate: full-registry validation with defn threading ──


def validate_member_actions(
    forms: tuple[Form, ...], *, registry: dict[str, Builtin], scope: set[str]
) -> None:
    """Statically validate a rule's action-form sequence (ADR 0021).

    The action position is no longer syntax-partitioned: any expression
    validates here, and a function body may carry actions — "where
    actions may occur" is not statically enumerable, so convergence is a
    runtime fact (the on-complete collector and the deterministic rules'
    return-value collection). What remains is full-registry validation:
    every form is checked by :func:`tlosure.validate.validate_form`
    against the *full* member registry (action builtins included) under
    ``scope``, so unregistered vocabulary and undefined symbols are
    still rejected at load — plus sequential ``defn`` threading: a
    ``defn`` head form adds its name to the scope the *following* forms
    see (define-before-use), mirroring the runtime's shared env dict.
    """
    local = set(scope)
    for form in forms:
        if _is_defn(form):
            local.add(_validate_defn(form, registry, local, None))
            continue
        validate_form(form, registry, local)
