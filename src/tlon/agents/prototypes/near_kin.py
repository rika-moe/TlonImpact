"""Near-kin rejection (ADR 0014): cross-bloodline duplicate detection via
stdlib text similarity.
"""

import difflib

from tlon.agents.prototypes.info import BEHAVIOR_FILE
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths

logger = get_logger(__name__)


# ==================== Near-kin rejection (ADR 0014) ==================

NEAR_KIN_THRESHOLD = 0.9
"""Similarity cutoff for near-kin rejection — deliberately high: only
reskin-level duplicates (a proposal that is textually almost identical to
an existing prototype) are turned away, and only **across bloodlines**:
the declared parent is exempt (see ``find_near_kin``'s ``exclude``), so a
generational micro-tweak along its own lineage passes.

Measured anchor ( smoke case): a scribe-derived spec whose only change
is the reflect interval 15 → 30 scores 0.996 after normalization — caught
when compared against the population at large, released when the near-kin
is the proposal's own parent. Substantive rewrites land far below 0.9.
"""


def _normalize_behavior_text(text: str) -> str:
    """Lightweight normalization before similarity comparison.

    Drops EDN comment lines (``;``-prefixed) and collapses every whitespace
    run to a single space, so reformatting alone can neither dodge nor
    trigger the gate. The text is otherwise compared verbatim — no EDN
    parse, so write-side templates carrying ``${var}`` placeholders compare
    fine as plain text.
    """
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith(";")]
    return " ".join(" ".join(lines).split())


def find_near_kin(
    behavior_text: str,
    exclude: str | None = None,
    *,
    threshold: float = NEAR_KIN_THRESHOLD,
    tlon_paths: TlonPaths,
) -> tuple[str, float] | None:
    """Find the most similar existing prototype across both areas.

    Compares ``behavior_text`` against every ``behavior.tlon`` in the
    trusted library and the staging area with
    ``difflib.SequenceMatcher.ratio`` on normalized text (pure stdlib text
    similarity — behavior specs are small canonical EDN texts, so an
    embedding model would buy nothing here).

    Args:
        behavior_text: raw behavior spec text of the proposal
        exclude: prototype name to skip — the proposal's declared parent.
            A child resembling its parent is the whole point of the
            generation channel (NEAT-style speciation protection:
            innovation is protected for a few generations inside its own
            bloodline); what the gate rejects is cross-bloodline
            convergence, not micro-evolution along a lineage.
        threshold: minimum ratio that counts as near-kin
        tlon_paths: path resolver for the target layout

    Returns:
        ``(prototype_name, ratio)`` of the closest match when its ratio
        reaches ``threshold``, else None (also None for an empty library or
        an empty/blank proposal text). Unreadable prototypes are skipped —
        a broken file must never block a proposal.
    """
    p = tlon_paths
    candidate = _normalize_behavior_text(behavior_text)
    if not candidate:
        return None
    best: tuple[str, float] | None = None
    for root in (p.prototypes_dir(), p.prototypes_staging_dir()):
        if not root.exists():
            continue
        for proto_dir in sorted(root.iterdir()):
            behavior_file = proto_dir / BEHAVIOR_FILE
            if not proto_dir.is_dir() or not behavior_file.exists():
                continue
            if proto_dir.name == exclude:
                continue
            try:
                existing = _normalize_behavior_text(
                    behavior_file.read_text(encoding="utf-8")
                )
            except OSError as e:
                logger.warning("near_kin_unreadable", path=str(behavior_file), error=str(e))
                continue
            if not existing:
                continue
            ratio = difflib.SequenceMatcher(None, candidate, existing).ratio()
            if best is None or ratio > best[1]:
                best = (proto_dir.name, ratio)
    if best is not None and best[1] >= threshold:
        return best
    return None
