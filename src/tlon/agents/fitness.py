"""Bloodline fitness for spawn-budget tilt (issue , ADR 0014).

A bloodline's fitness is *derived, never stored* — same discipline as the
reputation () and ledger () read sides. Two derived signals are
joined per bloodline (key = ``AgentConfig.prototype``):

- consumption: ``collect_lineage_ledgers`` (``base/ledger.py``) gives the
  bloodline's total token spend;
- production: ``collect_reputation`` (``agents/reputation.py``) gives each
  member's ``endorsements_received``, summed over the bloodline.

Bloodline value = Σ_members (1 + endorsements_received) — every member
counts once by existing, endorsements add on top. Efficiency =
value / max(total_tokens, 1).

``tilt_for`` turns efficiencies into a quota multiplier: the bloodline's
efficiency relative to the society mean, clamped to [0.5, 2.0]. Cold
start (no data, or every bloodline at zero) is neutral 1.0 — the system
behaves exactly as before the tilt existed until evidence accumulates.

The tilt is continuous; the solvency gate () is its binary
counterpart, derived from the same efficiency table: a bloodline is
*insolvent* only when both (a) its total token spend reaches
``_MIN_SAMPLE_TOKENS`` (below that the evidence is too thin — presumption
of innocence) and (b) its efficiency falls below ``SOLVENCY_RATIO`` × the
society mean (long-term outgoings exceed endorsed income). Insolvent
bloodlines lose proposal eligibility (``writePrototype`` /
``proposeBehavior``) and spawn eligibility; the tilt's floor is 0.5, not
0, because starvation/extinction is this gate's job — the tilt only
tilts, the gate decides life and death.

Everything fails open: any error in the derivation yields the neutral
tilt 1.0 / the empty insolvent set — fitness bookkeeping must never
block a spawn or a proposal.
"""

from collections.abc import Awaitable, Callable

from tlon.agents.reputation import collect_reputation
from tlon.base.ledger import collect_lineage_ledgers
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths, paths

logger = get_logger(__name__)

#: Tilt bounds: an efficient bloodline's spawn quota at most doubles, an
#: inefficient one's at most halves. No 0 floor in v1 — extinction is the
#: solvency gate's job ().
TILT_MIN = 0.5
TILT_MAX = 2.0

#: Neutral tilt: cold start and every failure path.
TILT_NEUTRAL = 1.0

#: Solvency sample floor (): a bloodline that has spent fewer tokens
#: than this is never insolvent — the evidence is too thin to convict
#: (presumption of innocence). 100k tokens is orders of magnitude above a
#: single session's spend, so only long-lived bloodlines can be judged.
_MIN_SAMPLE_TOKENS = 100_000

#: Solvency threshold (): insolvent = efficiency below this fraction
#: of the society mean. 0.25 is deliberately conservative — the gate
#: condemns only bloodlines four times worse than average, erring on the
#: side of letting marginal ones live.
SOLVENCY_RATIO = 0.25


async def lineage_efficiency(tlon_paths: TlonPaths) -> dict[str, float]:
    """Derive per-bloodline efficiency (value per token) from raw files.

    Args:
        tlon_paths: Home layout resolver (ledger files, mailboxes, configs)

    Returns:
        bloodline name -> efficiency. Empty on any failure (fail-open).
    """
    try:
        lineages = await collect_lineage_ledgers(tlon_paths=tlon_paths)
        member_ids = [m for lineage in lineages.values() for m in lineage["members"]]
        efficiencies: dict[str, float] = {}
        for name, lineage in lineages.items():
            value = 0
            for member_id in lineage["members"]:
                report = await collect_reputation(
                    member_id, tlon_paths=tlon_paths, member_ids=member_ids
                )
                value += 1 + report["endorsements_received"]
            efficiencies[name] = value / max(lineage["total_tokens"], 1)
        return efficiencies
    except Exception as e:  # fail-open: fitness must never block a spawn
        logger.warning("lineage_efficiency_failed", error=str(e))
        return {}


def tilt_for(efficiencies: dict[str, float], lineage: str) -> float:
    """Quota multiplier for one bloodline from the efficiency table.

    Neutral 1.0 on cold start (empty table, every efficiency zero, or the
    bloodline absent from the data); otherwise the bloodline's efficiency
    relative to the society mean, clamped to [``TILT_MIN``, ``TILT_MAX``].
    """
    if lineage not in efficiencies or not any(efficiencies.values()):
        return TILT_NEUTRAL
    mean = sum(efficiencies.values()) / len(efficiencies)
    if mean <= 0:
        return TILT_NEUTRAL
    return min(max(efficiencies[lineage] / mean, TILT_MIN), TILT_MAX)


def tilt_provider(tlon_paths: TlonPaths | None) -> Callable[[str], Awaitable[float]]:
    """Bind the tilt derivation to a paths layout (the spawn-budget input).

    The bloodline key is the prototype name itself
    (``collect_lineage_ledgers`` keys by ``AgentConfig.prototype``), so
    the provider's input is the spawn target's prototype. Recomputed on
    every call — spawn is a low-frequency operation, so no caching.
    """
    resolved = tlon_paths or paths

    async def tilt(prototype: str) -> float:
        return tilt_for(await lineage_efficiency(resolved), prototype)

    return tilt


async def insolvent_lineages(tlon_paths: TlonPaths) -> set[str]:
    """Bloodlines that have lost solvency (): proposal/spawn gate input.

    A bloodline is insolvent only when BOTH hold:

    - its ``total_tokens`` reaches ``_MIN_SAMPLE_TOKENS`` (below the
      floor the evidence is too thin — presumption of innocence);
    - its efficiency is below ``SOLVENCY_RATIO`` × the society mean
      (long-term outgoings exceed endorsed income).

    Returns:
        Set of insolvent bloodline names. Empty on no data, on
        society-wide zero consumption, and on any failure (fail-open,
        same discipline as the tilt).
    """
    try:
        lineages = await collect_lineage_ledgers(tlon_paths=tlon_paths)
        efficiencies = await lineage_efficiency(tlon_paths)
        if not efficiencies or not any(efficiencies.values()):
            return set()
        mean = sum(efficiencies.values()) / len(efficiencies)
        return {
            name
            for name, lineage in lineages.items()
            if name in efficiencies
            and lineage["total_tokens"] >= _MIN_SAMPLE_TOKENS
            and efficiencies[name] < SOLVENCY_RATIO * mean
        }
    except Exception as e:  # fail-open: bookkeeping must never gate anyone
        logger.warning("insolvent_lineages_failed", error=str(e))
        return set()


def solvency_provider(tlon_paths: TlonPaths | None) -> Callable[[str], Awaitable[bool]]:
    """Bind the solvency derivation to a paths layout (gate input).

    Returns True (solvent) for any bloodline not in
    ``insolvent_lineages`` — including unknown ones (a bloodline with no
    ledger is innocent by construction). Recomputed on every call, same
    as ``tilt_provider``.
    """
    resolved = tlon_paths or paths

    async def solvent(prototype: str) -> bool:
        return prototype not in await insolvent_lineages(resolved)

    return solvent
