"""Trust ladder for members (issue ): graduated autonomy for the spawned.

A member's trust level is *derived, never stored* — same discipline as the
reputation (), lineage () and fitness (/) read sides. v1 has
two levels:

- **probation** (见习): the member was spawned (``AgentConfig.spawned_by`` is
  not None) and has not yet earned a single endorsement
  (``endorsements_received < ENDORSEMENT_THRESHOLD``);
- **full** (正式): human-placed (``spawned_by is None``) or endorsed at least
  once.

Promotion takes effect on the member's next wake: ``assemble_cognitive``
re-derives the level on every assembly, which a scale-to-zero world performs
naturally.

Probation tightens three gates (docs/design/unified-member-model.md 信任阶梯):

1. spawn quota: the spawner-keyed ``probation`` provider caps a probationary
   spawner's effective per-session quota at 1 (``agents/spawn.py``);
2. tool subset: probationary members are assembled without the four
   reproduction/evolution tools (:data:`REPRODUCTION_TOOLS`;
   ``reviewPrototype`` stays — advisory only);
3. mentor cc: outbound mail of a probationary member is cc'd to its mentor
   (the spawner) on the two composed-mail paths (EffectExecutor.send_mail and
   MemberRuntime._send_mail); forwarding is not cc'd (passing along an
   existing mail the mentor plausibly already saw is noise).

Known gap: the DSL ``stop`` action is not gated — the policy gate for stop
requests is Adam-side / future work. The DSL ``spawn`` action needs no
separate tool gate: it flows through the SpawnManager's probation quota
(same funnel).

Everything fails open: a derivation error yields full trust — availability
first, same discipline as the fitness providers.
"""

from collections.abc import Awaitable, Callable

from tlon.agents.reputation import collect_reputation
from tlon.base.config import yaml_load_all
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths, paths

logger = get_logger(__name__)

#: A spawned member turns full at its first endorsement. Finer ladders
#: (more endorsements, graduated caps) wait for usage data.
ENDORSEMENT_THRESHOLD = 1

#: The reproduction/evolution tools withheld from probationary members at
#: assembly time (). ``reviewPrototype`` is deliberately absent:
#: reviewing a staging proposal is advisory (approve stays human).
REPRODUCTION_TOOLS: frozenset[str] = frozenset(
    {"spawnAgent", "stopAgent", "writePrototype", "proposeBehavior"}
)


async def is_full_member(agent_id: str, *, tlon_paths: TlonPaths) -> bool:
    """Derive one member's trust level: True = full, False = probation.

    Human-placed members (no ``spawned_by``) are full by construction; a
    member without an on-disk config is not ours to gate and also passes.
    Spawned members are full once their derived reputation
    (``agents/reputation.py``) shows at least ``ENDORSEMENT_THRESHOLD``
    endorsements received. The society roster is re-read per call, so the
    scan always sees the current society.
    """
    configs = yaml_load_all(tlon_paths.tlon_home())
    config = configs.get(agent_id)
    if config is None or config.spawned_by is None:
        return True
    member_ids = sorted(configs) + tlon_paths.known_human_ids()
    report = await collect_reputation(agent_id, tlon_paths=tlon_paths, member_ids=member_ids)
    return bool(report["endorsements_received"] >= ENDORSEMENT_THRESHOLD)


def trust_provider(tlon_paths: TlonPaths | None) -> Callable[[str], Awaitable[bool]]:
    """Bind the trust derivation to a paths layout (the is-full predicate).

    Keyed by **member id** — unlike the tilt/solvency providers
    (``agents/fitness.py``), which are keyed by the spawn target's
    prototype. Recomputed on every call — assembly and spawn are
    low-frequency, so no caching. Fail-open: any derivation error yields
    full trust (availability first), logged as a warning.
    """
    resolved = tlon_paths or paths

    async def is_full(agent_id: str) -> bool:
        try:
            return await is_full_member(agent_id, tlon_paths=resolved)
        except Exception as e:  # fail-open: trust bookkeeping must never gate
            logger.warning("trust_derivation_failed", agent_id=agent_id, error=str(e))
            return True

    return is_full


def probation_provider(tlon_paths: TlonPaths | None) -> Callable[[str], Awaitable[bool]]:
    """The is-probation predicate, keyed by **spawner id** (SpawnManager input).

    The negation of :func:`trust_provider`; the fail-open direction flips
    with it — a derivation error reads as *not* probation, so a broken
    derivation never tightens anyone's quota.
    """
    is_full = trust_provider(tlon_paths)

    async def probation(agent_id: str) -> bool:
        return not await is_full(agent_id)

    return probation
