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
The floor is 0.5, not 0: starvation/extinction is the solvency gate's
job (); this module only tilts.

Everything fails open: any error in the derivation yields the neutral
tilt 1.0 — a fitness bookkeeping failure must never block a spawn.
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
