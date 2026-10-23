"""Society policy context — the fifth evaluation context (ADR 0022).

Design: ``docs/design/society-policy.md``. The first pull-typed context:
instead of reacting to an event, the host calls the policy file's hooks
at fixed evaluation points (spawn / staging proposal / assembly). The
policy is data — ``~/.tlon/policy.tlon`` (``paths.policy_file()``),
human-written only (outside every member's ``allowed_paths`` and the
prototype staging channel).

Mechanism/policy split: the host materializes raw social facts
(``materialize_lineages`` / ``materialize_member`` — collection is
mechanism) and the policy file judges them (the formulas and thresholds
are policy). No callback query ports: social data collection is async
IO, so the hooks receive materialized data as positional arguments
(:data:`~tlon.agents.vocabulary.POLICY_BUILTINS` is the pure language
subset — no actions, no host queries).

File shape (``POLICY_VERSION``)::

    {:version 1
     :policy [(defn helper [x] ...)      ; shared definitions thread forward
              (defn tilt [subject lineages] ...)
              (defn solvent? [subject lineages] ...)
              (defn full-member? [member] ...)]}

The ``:policy`` vector is a form sequence evaluated in one shared env
(the ADR 0021 defn-threading discipline, validated by
the ordinary sequence validator against the policy registry). The env starts
from the *default* policy's definitions, so a society file overrides
hooks à la carte — a hook it does not redefine keeps the default
implementation — and may reuse the default helpers. The hook contract is
the reserved names in
:data:`~tlon.agents.vocabulary.POLICY_HOOK_NAMES`.

Fail-open (the fitness/trust discipline, unchanged): a missing,
unparseable, invalid or failing policy file yields the default policy;
any hook evaluation error (fuel exhaustion included) yields the hook's
safe default at the provider level — tilt 1.0, solvent, full trust.
Policy bookkeeping never blocks a spawn or an assembly.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from tlon.agents.reputation import collect_reputation
from tlon.agents.vocabulary import (
    POLICY_BUILTINS,
    LineageMemberStats,
    LineageStats,
    PolicyMemberInfo,
)
from tlon.base.config import yaml_load_all
from tlon.base.ledger import collect_lineage_ledgers
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.organization.mail.store import MailStore
from tlosure.builtins import EvalError
from tlosure.edn import EDNParseError, Form, Keyword, Symbol, loads
from tlosure.eval import DEFAULT_MAX_DEPTH, MAX_FUEL, Evaluator
from tlosure.validate import _validate_seq

logger = get_logger(__name__)

POLICY_VERSION = 1

#: One context-level fuel cap for both loading and hook calls — the
#: policy file is human-written governance, not an agent's self-declared
#: budget, so there is no per-hook fuel knob.
POLICY_FUEL = MAX_FUEL

#: Fail-open defaults per hook (the pre-migration constants from
#: fitness.py / trust.py).
TILT_NEUTRAL = 1.0

#: The default policy: an exact replica of the fitness.py / trust.py
#: formulas this context replaces (tilt clamp [0.5, 2.0]; insolvent only
#: when total_tokens >= 100k AND efficiency < 0.25 × society mean; full
#: member = human-placed or endorsed at least once). With no
#: policy.tlon the behavior is byte-identical to the pre-migration code.
DEFAULT_POLICY_EDN = r"""{:version 1
 :policy [;; bloodline value = Σ(1 + endorsements_received) over its members
          (defn lineage-value [l]
            (reduce acc m (+ acc 1 (get m :endorsements_received)) 0 (get l :members)))
          ;; efficiency = value / max(total_tokens, 1)
          (defn efficiency [l]
            (/ (lineage-value l)
               (if (> (get l :total_tokens) 1) (get l :total_tokens) 1)))
          (defn society-mean-efficiency [lineages]
            (if (= 0 (count lineages))
              0
              (/ (reduce a l (+ a (efficiency l)) 0 lineages)
                 (count lineages))))
          ;; tilt: efficiency relative to the society mean, clamped [0.5, 2.0];
          ;; cold start (no subject data / zero mean) is neutral 1.0
          (defn tilt [subject lineages]
            (if (= nil subject)
              1.0
              (let [mean (society-mean-efficiency lineages)]
                (if (<= mean 0)
                  1.0
                  (let [t (/ (efficiency subject) mean)]
                    (if (< t 0.5) 0.5 (if (> t 2.0) 2.0 t)))))))
          ;; solvency: insolvent only when the sample is big enough
          ;; (>= 100k tokens — presumption of innocence below) AND the
          ;; efficiency falls below 0.25 × the society mean
          (defn solvent? [subject lineages]
            (if (= nil subject)
              true
              (if (< (get subject :total_tokens) 100000)
                true
                (let [mean (society-mean-efficiency lineages)]
                  (if (<= mean 0)
                    true
                    (>= (efficiency subject) (* 0.25 mean)))))))
          ;; trust: human-placed (no spawned_by) or endorsed at least once
          (defn full-member? [member]
            (or (= nil (get member :spawned_by))
                (>= (get member :endorsements_received) 1)))]}
"""


class PolicyValidationError(ValueError):
    """A policy file failed structural or vocabulary validation."""


@dataclass(frozen=True)
class SocietyPolicy:
    """A loaded policy: the shared env holding the evaluated definitions.

    The env always contains the default policy's definitions (they are
    evaluated first); a society file's forms thread into it and may
    override hooks or helpers.
    """

    env: dict[str, Any]

    def call(self, hook: str, *args: Any) -> Any:
        """Apply one hook to materialized arguments.

        The call is an ordinary form application in the policy env
        extended with positional argument bindings, so hooks defined by
        ``defn``/``fn`` and their shared helpers resolve through the
        evaluator's normal closure machinery. Each call draws from a
        fresh :data:`POLICY_FUEL` budget.
        """
        if hook not in self.env:
            raise EvalError(f"policy hook {hook!r} is not defined")
        bindings = {f"__arg{index}": value for index, value in enumerate(args)}
        form = Form([Symbol(hook), *[Symbol(name) for name in bindings]])
        return Evaluator(POLICY_BUILTINS, POLICY_FUEL, DEFAULT_MAX_DEPTH).eval(
            form, {**self.env, **bindings}, 0
        )


def _parse_policy(text: str) -> tuple[Form, ...]:
    """Structural validation: a top-level map with :version and :policy."""
    data = loads(text)
    if isinstance(data, Form) or not isinstance(data, dict):
        raise PolicyValidationError(
            f"policy must contain a top-level map, got {type(data).__name__}"
        )
    version = data.get(Keyword("version"))
    if version != POLICY_VERSION:
        raise PolicyValidationError(
            f"unsupported :version {version!r}; only version {POLICY_VERSION} is supported"
        )
    raw = data.get(Keyword("policy"))
    if raw is None:
        return ()
    if isinstance(raw, Form) or not isinstance(raw, (list, tuple)):
        raise PolicyValidationError(f":policy must be a vector of forms, got {type(raw).__name__}")
    for item in raw:
        if not isinstance(item, Form) or not item:
            raise PolicyValidationError(
                f":policy items must be non-empty forms, got {type(item).__name__}"
            )
    return tuple(raw)


def _eval_policy_forms(forms: tuple[Form, ...], env: dict[str, Any]) -> None:
    """Evaluate the form sequence in ``env`` (shared dict: defn threading).

    One evaluator for the whole sequence — the load draws from a single
    :data:`POLICY_FUEL` budget.
    """
    evaluator = Evaluator(POLICY_BUILTINS, POLICY_FUEL, DEFAULT_MAX_DEPTH)
    for form in forms:
        evaluator.eval(form, env, 0)


def _load_default() -> SocietyPolicy:
    """The default policy env; raises only on a code bug (the EDN is ours)."""
    forms = _parse_policy(DEFAULT_POLICY_EDN)
    _validate_seq(list(forms), POLICY_BUILTINS, set(), None)
    env: dict[str, Any] = {}
    _eval_policy_forms(forms, env)
    return SocietyPolicy(env)


def load_policy(tlon_paths: TlonPaths) -> SocietyPolicy:
    """Load the society policy: defaults first, policy.tlon threaded on top.

    An absent file means the default policy (the normal state). A file
    that fails parsing, validation or evaluation is rejected wholesale
    with a warning — a half-evaluated env never leaks into policy
    decisions (fail-open to the pristine default).

    Validation covers the *concatenated* sequence (default forms + the
    file's forms): an overriding hook may reuse the default helpers
    (``efficiency`` & co.), so the validator must see the same names the
    runtime env will hold.
    """
    path = tlon_paths.policy_file()
    if not path.exists():
        return _load_default()
    try:
        default_forms = _parse_policy(DEFAULT_POLICY_EDN)
        forms = _parse_policy(path.read_text(encoding="utf-8"))
        _validate_seq(list(default_forms + forms), POLICY_BUILTINS, set(), None)
        policy = _load_default()
        _eval_policy_forms(forms, policy.env)
        return policy
    except (EDNParseError, PolicyValidationError, EvalError, OSError) as e:
        logger.warning("policy_file_rejected", error=str(e))
        return _load_default()


# ── Data materialization (mechanism: collect raw facts, never judge) ──


async def materialize_lineages(tlon_paths: TlonPaths) -> list[LineageStats]:
    """All bloodlines' raw stats: token totals plus per-member endorsements.

    Same raw sources as the pre-migration fitness.py: lineage token
    aggregates from ``collect_lineage_ledgers``, endorsement counts from
    ``collect_reputation``. The value/efficiency formulas derived from
    these facts live in the policy file, not here.
    """
    lineages = await collect_lineage_ledgers(tlon_paths=tlon_paths)
    member_ids = [m for lineage in lineages.values() for m in lineage["members"]]
    out: list[LineageStats] = []
    for name, lineage in lineages.items():
        members: list[LineageMemberStats] = []
        for member_id in lineage["members"]:
            report = await collect_reputation(
                member_id, tlon_paths=tlon_paths, member_ids=member_ids
            )
            members.append(
                LineageMemberStats(
                    id=member_id, endorsements_received=report["endorsements_received"]
                )
            )
        out.append(LineageStats(name=name, total_tokens=lineage["total_tokens"], members=members))
    return out


async def materialize_member(agent_id: str, *, tlon_paths: TlonPaths) -> PolicyMemberInfo | None:
    """One member's social facts; None when the member has no on-disk config.

    A config-less member is not ours to gate — the trust provider passes
    it as full without consulting the policy (the pre-migration
    discipline of ``is_full_member``).
    """
    configs = yaml_load_all(tlon_paths.tlon_home())
    config = configs.get(agent_id)
    if config is None:
        return None
    member_ids = sorted(configs) + await asyncio.to_thread(
        MailStore(tlon_paths.mail_database_file()).known_human_ids
    )
    report = await collect_reputation(agent_id, tlon_paths=tlon_paths, member_ids=member_ids)
    return PolicyMemberInfo(
        id=agent_id,
        spawned_by=config.spawned_by,
        endorsements_received=report["endorsements_received"],
    )


# ── Providers (same signatures as the fitness.py / trust.py providers) ──
#
# The provider interfaces are the mechanism; the formulas are policy.
# Each provider loads the policy per call — spawn and assembly are
# low-frequency operations, same recomputation discipline as the
# pre-migration providers — and every failure (load, materialization,
# hook evaluation) fails open to the hook's safe default.


def policy_tilt_provider(tlon_paths: TlonPaths) -> Callable[[str], Awaitable[float]]:
    """Spawn-budget tilt from the policy's ``tilt`` hook (fail-open 1.0)."""
    resolved = tlon_paths

    async def tilt(prototype: str) -> float:
        try:
            policy = load_policy(resolved)
            lineages = await materialize_lineages(resolved)
            subject = next((lineage for lineage in lineages if lineage["name"] == prototype), None)
            result = policy.call("tilt", subject, lineages)
            if isinstance(result, bool) or not isinstance(result, (int, float)):
                raise EvalError(f"tilt must return a number, got {type(result).__name__}")
            return float(result)
        except Exception as e:  # fail-open: policy never blocks a spawn
            logger.warning("policy_tilt_failed", prototype=prototype, error=str(e))
            return TILT_NEUTRAL

    return tilt


def policy_solvency_provider(tlon_paths: TlonPaths) -> Callable[[str], Awaitable[bool]]:
    """Solvency gate from the policy's ``solvent?`` hook (fail-open True)."""
    resolved = tlon_paths

    async def solvent(prototype: str) -> bool:
        try:
            policy = load_policy(resolved)
            lineages = await materialize_lineages(resolved)
            subject = next((lineage for lineage in lineages if lineage["name"] == prototype), None)
            return bool(policy.call("solvent?", subject, lineages))
        except Exception as e:  # fail-open: policy never gates anyone
            logger.warning("policy_solvency_failed", prototype=prototype, error=str(e))
            return True

    return solvent


def policy_trust_provider(tlon_paths: TlonPaths) -> Callable[[str], Awaitable[bool]]:
    """The is-full predicate from the policy's ``full-member?`` hook.

    Fail-open True — a derivation error reads as full trust (never
    tightened), the pre-migration discipline of ``trust_provider``.
    """
    resolved = tlon_paths

    async def is_full(agent_id: str) -> bool:
        try:
            info = await materialize_member(agent_id, tlon_paths=resolved)
            if info is None:
                return True
            policy = load_policy(resolved)
            return bool(policy.call("full-member?", info))
        except Exception as e:  # fail-open: trust bookkeeping never gates
            logger.warning("policy_trust_failed", agent_id=agent_id, error=str(e))
            return True

    return is_full


def policy_probation_provider(tlon_paths: TlonPaths) -> Callable[[str], Awaitable[bool]]:
    """The is-probation predicate, keyed by **spawner id** (SpawnManager input).

    The negation of :func:`policy_trust_provider`; the fail-open
    direction flips with it — a derivation error reads as *not*
    probation, so a broken policy never tightens anyone's quota.
    """
    is_full = policy_trust_provider(tlon_paths)

    async def probation(agent_id: str) -> bool:
        return not await is_full(agent_id)

    return probation
