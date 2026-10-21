"""Member vocabulary and explicit data scopes for behavior conditions.

All ``:when``, ``:until`` and stage ``:if`` expressions share the pure
projection of the member operation directory. Context differences are data
bindings, never separate function lists. Host queries and writes stay in the
ordinary program vocabulary; conditions receive materialized JSON records.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Protocol, TypedDict

from tlon.operations.control import validate_control_forms
from tlon.operations.definitions import host_operation_definitions
from tlon.operations.schema import InputError, json_value
from tlon.organization.mail.mail import Mail, address_for_member
from tlosure.builtins import (
    ARITHMETIC_BUILTINS,
    CORE_BUILTINS,
    ActionRequest,
    Builtin,
    EvalError,
)
from tlosure.edn import Form, Keyword
from tlosure.eval import DEFAULT_FUEL, current_evaluation, eval_form
from tlosure.validate import _is_defn, _validate_defn, validate_form

# ── Environment contracts (the data each context may see) ──
#
# Every evaluation context binds a fixed set of symbols with a fixed
# shape; these TypedDicts are those shapes and the builders below are the
# single construction point (the engine call sites delegate here).
# Load-time validation and runtime evaluation use these same explicit scopes.


class SelfEnv(TypedDict):
    """The ``self`` binding: the evaluating member's identity.

    ``address`` is pre-derived at construction so DSL builtins read data,
    never call back into tlon (ADR 0018).
    """

    id: str
    name: str
    address: str


class RuleEnv(TypedDict):
    """Rule evaluation context (``:when`` gates and ``:actions``).

    ``msg`` is the payload's materialized mail record, or None for non-mail
    events. ``event`` contains the same materialized payload; no live mail
    or service objects enter the condition evaluator.
    """

    msg: dict[str, Any] | None
    event: Any
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
    """session ``:until`` context (``loop_driver``)."""

    self: SelfEnv
    state: LoopStateEnv
    limits: LoopLimitsEnv


class RoundEnv(TypedDict):
    """The ``round`` binding: facts about the round being gated."""

    completing: bool
    sending_mail: bool


class StageCondEnv(TypedDict):
    """Stage ``:if`` context (``loop_driver``): loop state, the session's
    loop scratchpad, and the round's completion flag."""

    state: LoopStateEnv
    scratch: dict[str, Any]
    round: RoundEnv


#: Load-time validation scope for ``:when`` gates (mirrors RuleEnv).
RULE_SCOPE: frozenset[str] = frozenset({"msg", "event", "self"})

#: Actions use the same bindings as the event gate.
ACTION_SCOPE: frozenset[str] = RULE_SCOPE

LOOP_SCOPE: frozenset[str] = frozenset({"self", "state", "limits"})
STAGE_SCOPE: frozenset[str] = frozenset({"state", "scratch", "round"})


def self_env(member_id: str, name: str) -> SelfEnv:
    """The ``self`` binding for any context."""
    return {"id": member_id, "name": name, "address": address_for_member(member_id)}


def rule_env(payload: Any, *, member_id: str, name: str) -> RuleEnv:
    """The rule evaluation env (``MemberRuntime._env`` delegates here)."""
    mail = payload.get("mail") if isinstance(payload, dict) else None
    return {
        "msg": _materialize(mail),
        "event": _materialize(payload),
        "self": self_env(member_id, name),
    }


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
    sending_mail: bool = False,
) -> StageCondEnv:
    """The stage ``:if`` env (``loop_driver`` delegates here)."""
    return {
        "state": {
            "iteration": iteration,
            "error_count": error_count,
            "token_usage": token_usage,
        },
        "scratch": scratch,
        "round": {"completing": completing, "sending_mail": sending_mail},
    }


def _materialize(value: Any) -> Any:
    """Copy event data, projecting mail into its public serialized record."""
    if isinstance(value, Mail):
        return _materialize(value.to_dict())
    if isinstance(value, dict):
        return {key: _materialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_materialize(item) for item in value]
    return value


def condition_builtins(registry: dict[str, Builtin]) -> dict[str, Builtin]:
    """One pure projection for every condition; read-only I/O is not pure."""
    return {name: builtin for name, builtin in registry.items() if builtin.pure}


def validate_condition(form: Any, registry: dict[str, Builtin], scope: frozenset[str]) -> None:
    """Validate all branches, including local function bodies, with pure words."""
    validate_form(form, condition_builtins(registry), scope=set(scope))


def condition_env(env: dict[str, Any], scope: frozenset[str]) -> dict[str, Any]:
    """Require the complete context and admit only explicit JSON data bindings."""
    missing = scope - env.keys()
    if missing:
        raise EvalError(f"condition context missing bindings: {', '.join(sorted(missing))}")
    try:
        return {name: json_value(env[name], edn_keys=True) for name in scope}
    except InputError as exc:
        raise EvalError(f"condition context must contain materialized data: {exc}") from exc


def evaluate_condition(
    form: Any,
    env: dict[str, Any],
    registry: dict[str, Builtin],
    scope: frozenset[str],
    *,
    fuel: int = DEFAULT_FUEL,
) -> Any:
    """Use the shared language evaluator after the common purity/data gates."""
    validate_condition(form, registry, scope)
    return eval_form(form, condition_env(env, scope), condition_builtins(registry), fuel=fuel)


def _builtin_from(env: dict[str, Any], msg: Any, address: Any) -> bool:
    """Compare an explicit mail record's sender with an explicit address."""
    return isinstance(msg, dict) and isinstance(address, str) and msg.get("from_address") == address


def _builtin_content_matches(env: dict[str, Any], target: Any, pattern: Any) -> bool:
    """Regex-search a mail's content or a plain string (e.g. ``result``).

    ``target=None`` (a non-mail event's ``msg``) safely yields False;
    any other non-mail non-string target is a spec bug and raises.
    """
    if target is None:
        return False
    if isinstance(target, dict):
        content = target.get("content")
        if not isinstance(content, str):
            raise EvalError("content-matches? mail record content must be a string")
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


# ── Explicit pure member functions ──


def _mail_field(env: dict[str, Any], mail: Any, field: Any) -> Any:
    fields = {
        "id": "id",
        "from": "from_address",
        "to": "to",
        "cc": "cc",
        "subject": "subject",
        "content": "content",
        "timestamp": "timestamp",
        "thread-id": "thread_id",
        "in-reply-to": "in_reply_to",
        "forward-of": "forward_of",
        "endorsing": "endorsing",
    }
    if not isinstance(field, Keyword) or str(field) not in fields:
        raise EvalError(f"mail-field: unknown field {field!r}")
    if mail is None:
        return None
    if not isinstance(mail, dict):
        raise EvalError("mail-field expects a materialized mail record or nil")
    return mail.get(fields[str(field)])


SCRIPT_BUILTINS: dict[str, Builtin] = {
    **CORE_BUILTINS,
    **ARITHMETIC_BUILTINS,
    "mail-field": Builtin(_mail_field, pure=True),
    "from?": Builtin(_builtin_from, pure=True),
    "content-matches?": Builtin(_builtin_content_matches, pure=True),
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
        members); offline members and Adam are included through the delivery
        directory. The public names and addresses may be quoted and shared;
        private contact books, persona files and mail remain private."""
        ...


# ── Effect ports (DSL → host, effects-as-data) ──
#
# Action builtins validate their arguments during pure evaluation and
# produce ActionRequest data; the program driver executes each request
# and records its result before evaluation continues. Host quotas and
# trust gates remain outside the member program.


#: Effects available to every rule, independent of its event or other actions.
MEMBER_ACTION_NAMES = frozenset({"run-loop"}) | frozenset(
    operation.name for operation in host_operation_definitions()
)

_EFFECT_COLLECTOR: ContextVar[list[ActionRequest] | None] = ContextVar(
    "tlon_effect_collector", default=None
)


@contextmanager
def collect_member_effects() -> Iterator[list[ActionRequest]]:
    """Collect one synchronous evaluation, including isolated capability closures."""
    requests: list[ActionRequest] = []
    token = _EFFECT_COLLECTOR.set(requests)
    try:
        yield requests
    finally:
        _EFFECT_COLLECTOR.reset(token)


def _unavailable_operation(_env: dict[str, Any], *args: Any) -> Any:
    raise EvalError("Operation unavailable for this member")


def make_member_builtins(
    host: ScriptHostQueries | None = None,
    collector: list[ActionRequest] | None = None,
) -> dict[str, Builtin]:
    """Build the member vocabulary with optional effect collection.

    Operation names are static unavailable placeholders until DSLAdapter binds
    member services. Only run-loop constructs ActionRequest recipe data;
    an optional collector supports synchronous recipe analysis. Host queries
    are injected closures.
    """

    def _record(request: ActionRequest) -> ActionRequest:
        target = _EFFECT_COLLECTOR.get()
        if target is None:
            target = collector
        if target is not None:
            target.append(request)
        return request

    def _run_loop(env: dict[str, Any], recipe: Any) -> ActionRequest:
        from tlon.agents.member_spec import SessionRecipe, validate_session_recipe

        try:
            active = current_evaluation()
            registry = active[0].builtins if active is not None else builtins
            validate_session_recipe(SessionRecipe.from_edn(recipe), builtins=registry)
        except ValueError as exc:
            raise EvalError(str(exc)) from exc
        return _record(ActionRequest(kind="run_loop", data={"recipe": recipe}))

    builtins: dict[str, Builtin] = {
        **{
            operation.name: Builtin(fn=_unavailable_operation, pure=False)
            for operation in host_operation_definitions()
        },
        **SCRIPT_BUILTINS,
        "run-loop": Builtin(_run_loop, pure=False),
    }
    if host is not None:
        builtins["sleeping?"] = Builtin(lambda env, aid: host.is_sleeping(aid), pure=False)
        builtins["running?"] = Builtin(lambda env, aid: host.is_running(aid), pure=False)
        builtins["lazy?"] = Builtin(lambda env, aid: host.is_lazy(aid), pure=False)
        builtins["member-roster"] = Builtin(lambda env: host.member_roster(), pure=False)
    return builtins


# ── Static gate: full-registry validation with defn threading ──


def validate_member_actions(
    forms: tuple[Form, ...], *, registry: dict[str, Builtin], scope: set[str]
) -> None:
    """Statically validate a rule's action-form sequence (ADR 0021).

    The action position is no longer syntax-partitioned: any expression
    validates here, and a function body may carry actions — "where
    actions may occur" is not statically enumerable, so convergence is a
    runtime fact handled by the effect collector. What remains is full-registry validation:
    every form is checked by :func:`tlosure.validate.validate_form`
    against the *full* member registry (action builtins included) under
    ``scope``, so unregistered vocabulary and undefined symbols are
    still rejected at load — plus sequential ``defn`` threading: a
    ``defn`` head form adds its name to the scope the *following* forms
    see (define-before-use), mirroring the runtime's shared env dict.
    """
    validate_control_forms(forms)
    local = set(scope)
    for form in forms:
        if _is_defn(form):
            local.add(_validate_defn(form, registry, local, None))
            continue
        validate_form(form, registry, local)
