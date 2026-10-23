"""Ordinary member vocabulary, materialized events, and society policy data."""

import re
from typing import Any, Protocol, TypedDict

from tlon.operations.definitions import host_operation_definitions
from tlon.organization.mail.mail import Mail, address_for_member
from tlosure.builtins import (
    ARITHMETIC_BUILTINS,
    CORE_BUILTINS,
    Builtin,
    EvalError,
)
from tlosure.edn import Keyword

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


def rule_env(payload: Any, *, member_id: str, name: str) -> dict[str, Any]:
    """Materialize event data before passing it to an ordinary entry."""
    mail = payload.get("mail") if isinstance(payload, dict) else None
    return {
        "msg": _materialize(mail),
        "event": _materialize(payload),
        "self": self_env(member_id, name),
    }


def self_env(member_id: str, name: str) -> SelfEnv:
    return {"id": member_id, "name": name, "address": address_for_member(member_id)}


def _materialize(value: Any) -> Any:
    """Copy event data, projecting mail into its public serialized record."""
    if isinstance(value, Mail):
        return _materialize(value.to_dict())
    if isinstance(value, dict):
        return {key: _materialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_materialize(item) for item in value]
    return value


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


def _unavailable_operation(_env: dict[str, Any], *args: Any) -> Any:
    raise EvalError("Operation unavailable for this member")


def make_member_builtins(host: ScriptHostQueries | None = None) -> dict[str, Builtin]:
    """Build ordinary operation placeholders and explicit host query closures."""
    builtins: dict[str, Builtin] = {
        **{
            operation.name: Builtin(fn=_unavailable_operation, pure=False)
            for operation in host_operation_definitions()
        },
        **SCRIPT_BUILTINS,
        "tool-execute": Builtin(_unavailable_operation, pure=False),
    }
    if host is not None:
        builtins["sleeping?"] = Builtin(lambda env, aid: host.is_sleeping(aid), pure=False)
        builtins["running?"] = Builtin(lambda env, aid: host.is_running(aid), pure=False)
        builtins["lazy?"] = Builtin(lambda env, aid: host.is_lazy(aid), pure=False)
        builtins["member-roster"] = Builtin(lambda env: host.member_roster(), pure=False)
    return builtins
