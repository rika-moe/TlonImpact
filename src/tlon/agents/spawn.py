"""Spawn budget and derivation logic (split from agent_registry).

``SpawnManager`` owns the stage-6b spawn budget (global activation cap,
spawn depth, per-session quota) and executes the two member-derivation
flows: ``spawn_member`` (prototype instantiation under the budget) and
``adopt_agent`` (hot adoption of an out-of-process creation).

The per-session quota is tilted by the target bloodline's fitness
(ADR 0014): an optional ``tilt_provider`` maps the spawn target's
prototype to a multiplier (clamped to [0.5, 2.0] by the society policy's
``tilt`` hook, ``agents/policy.py`` — ADR 0022), and the effective quota
is ``ceil(session_spawn_quota * tilt)``. The global activation cap stays
untouched — splitting it per bloodline would punish incumbent members of
temporarily-inefficient lineages. Tilt 1.0 (the cold-start default, and
every failure fallback) reproduces the untilted behavior exactly.

The tilt is continuous; the solvency gate (ADR 0014) is binary: an optional
``solvency`` provider maps the spawn target's prototype to a bool, and
an insolvent bloodline (long-term token spend not covered by endorsed
output, judged by the policy's ``solvent?`` hook) is rejected outright,
right after the trust gate. Both fail open — fitness bookkeeping must
never block a spawn.

The probation gate (trust ladder, docs/design/unified-member-model.md)
tightens budget 3 from the
*spawner* side: an optional ``probation`` provider maps the spawner's
member id to a bool, and a probationary spawner (spawned, not yet
endorsed) gets its effective per-session quota capped at 1. Note the
keying difference from tilt/solvency: those two judge the spawn
*target's* bloodline (prototype name); probation judges the *spawner*
(member id) — one spawn can therefore be tilted by one bloodline's
fitness while capped by its spawner's trust level. Same fail-open
discipline: a missing provider or a derivation error reads as *not*
probation.

Of the three budgets, only the per-(spawner, session) quota is a ledger,
and it is persisted (``TlonPaths.spawn_quota_ledger_file``, atomic
replace): sessions themselves survive a restart via journal replay
(ADR 0007), so a memory-only quota would let a spawner refill its quota
by restarting. The activation cap is a runtime fact (the run table is
empty after a restart — persisting it would import dead members'
activations) and the spawn depth derives from lineage already persisted
in each member's ``agent.yaml`` (``spawn_depth``).

The registry is constructor-injected and used for the run table,
registration, and assembly callbacks; the import direction stays
one-directional (agent_registry → spawn, the back-reference is a type
annotation only).
"""

import json
import math
import os
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from tlon.agents.prototypes.catalog import get_prototype_info
from tlon.agents.prototypes.instantiate import instantiate
from tlon.base.config import AgentConfig, yaml_save
from tlon.base.events.event import Event, EventType
from tlon.base.logger import get_logger
from tlon.tools.agents.ports import SpawnRejectedError

if TYPE_CHECKING:
    from tlon.agents.agent_registry import AgentRegistry

logger = get_logger(__name__)

# Quota bucket used when no session context is available (e.g. direct calls
# from tests): all such spawns share one bucket per spawner.
_UNKNOWN_SESSION = "-"


class SpawnManager:
    """Spawn budget ledger + spawn/adopt execution for a registry.

    Created and owned by ``AgentRegistry``; the registry's public
    ``spawn_member`` / ``adopt_agent`` delegate here.
    """

    def __init__(
        self,
        registry: "AgentRegistry",
        max_activations: int,
        max_spawn_depth: int,
        session_spawn_quota: int,
        tilt_provider: Callable[[str], Awaitable[float]] | None = None,
        solvency: Callable[[str], Awaitable[bool]] | None = None,
        probation: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._registry = registry
        self._max_activations = max_activations
        self._max_spawn_depth = max_spawn_depth
        self._session_spawn_quota = session_spawn_quota
        # Bloodline-fitness tilt (ADR 0014): prototype name -> quota
        # multiplier. None = always neutral 1.0 (untilted behavior).
        self._tilt_provider = tilt_provider
        # Solvency gate (ADR 0014): prototype name -> solvent? None =
        # always solvent (gate disabled).
        self._solvency = solvency
        # Probation gate (trust ladder, docs/design/unified-member-model.md):
        # spawner member id -> on probation? None = never probation (gate
        # disabled). Keyed by the spawner, not the target prototype —
        # different dimension from tilt/solvency above.
        self._probation = probation
        # (spawner_id, session_id) -> successful spawn count, persisted to
        # disk so the anti-runaway quota survives restarts (sessions do,
        # via journal replay).
        self._ledger_path: Path = registry._paths.spawn_quota_ledger_file()
        self._quota: dict[tuple[str, str], int] = self._load_ledger()

    def reset(self) -> None:
        """Zero the in-memory quota ledger (registry shutdown).

        The persisted ledger file is deliberately left in place: the
        quota must survive a process restart or the anti-runaway cap
        would reset with it.
        """
        self._quota.clear()

    def _load_ledger(self) -> dict[tuple[str, str], int]:
        """Load the persisted quota ledger.

        A missing file is the normal first-run state. Corrupt data (bad
        JSON, wrong shape, malformed entries) warns and starts empty —
        a broken ledger must never block the server from booting.
        """
        path = self._ledger_path
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entries = raw.get("quota") if isinstance(raw, dict) else None
            if not isinstance(entries, list):
                raise ValueError("missing 'quota' list")
        except (OSError, ValueError) as e:
            logger.warning("spawn_quota_ledger_unreadable", file_path=str(path), error=str(e))
            return {}
        quota: dict[tuple[str, str], int] = {}
        for entry in entries:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("spawner"), str)
                and isinstance(entry.get("session"), str)
                and isinstance(entry.get("count"), int)
                and entry["count"] > 0
            ):
                quota[(entry["spawner"], entry["session"])] = entry["count"]
            else:
                logger.warning("spawn_quota_ledger_entry_skipped", entry=repr(entry))
        return quota

    def _save_ledger(self) -> None:
        """Persist the quota ledger (atomic replace: temp file + rename).

        A failed save logs and leaves the in-memory ledger authoritative
        for this process — the cap still holds until the next restart.
        """
        entries = [
            {"spawner": spawner, "session": session, "count": count}
            for (spawner, session), count in sorted(self._quota.items())
        ]
        payload = json.dumps({"version": 1, "quota": entries}, ensure_ascii=False) + "\n"
        tmp_path = self._ledger_path.with_suffix(".tmp")
        try:
            self._ledger_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, self._ledger_path)
        except OSError as e:
            logger.error(
                "spawn_quota_ledger_save_failed", file_path=str(self._ledger_path), error=str(e)
            )

    async def _tilt(self, prototype: str) -> float:
        """Quota tilt for the spawn target's bloodline (fail-open).

        A missing provider or any provider failure yields the neutral
        1.0 — fitness bookkeeping must never block a spawn.
        """
        if self._tilt_provider is None:
            return 1.0
        try:
            return float(await self._tilt_provider(prototype))
        except Exception as e:
            logger.warning("spawn_tilt_failed", prototype=prototype, error=str(e))
            return 1.0

    async def _solvent(self, prototype: str) -> bool:
        """Solvency of the spawn target's bloodline (fail-open, ADR 0014).

        A missing provider or any provider failure yields True — same
        discipline as ``_tilt``.
        """
        if self._solvency is None:
            return True
        try:
            return bool(await self._solvency(prototype))
        except Exception as e:
            logger.warning("spawn_solvency_failed", prototype=prototype, error=str(e))
            return True

    async def _probationary(self, spawner_id: str) -> bool:
        """Whether the *spawner* is on probation (fail-open,
        docs/design/unified-member-model.md).

        Keyed by spawner member id, unlike ``_tilt``/``_solvent`` which
        are keyed by the spawn target's prototype. A missing provider or
        any provider failure yields False — trust bookkeeping must never
        tighten a quota.
        """
        if self._probation is None:
            return False
        try:
            return bool(await self._probation(spawner_id))
        except Exception as e:
            logger.warning("spawn_probation_failed", spawner_id=spawner_id, error=str(e))
            return False

    async def spawn_member(
        self,
        prototype: str,
        name: str,
        spawner_id: str,
        session_id: str | None,
        params: dict[str, Any] | None = None,
    ) -> AgentConfig:
        """Spawn a new member from a prototype and start it immediately.

        Unified member model (ADR 0006): a spawned member is a member like
        any other — laziness/boot membership derive from the prototype's
        spec, and its home directory is never deleted (retirement
        archives, members only sleep). Lineage is kept for audit:
        ``spawned_by`` / ``spawn_depth`` / ``prototype``.

        Pipeline: budget checks -> id generation -> ``instantiate()``
        (writes persona + behavior + agent.yaml to disk) -> lineage fields
        -> ``reload_agent`` so the config is visible to the provider ->
        ``register`` + ``_start_agent`` -> ``AGENT_SPAWNED`` event.

        Permissions cannot escalate: the only source is the prototype
        library (trusted, Tlon-prebuilt or user-installed), and the
        behavior file is copied verbatim — a spec is total, so there is
        nothing to override and a spawner cannot grant capabilities beyond
        the prototype.

        Orphan policy (reparent, not kill): the member is registered
        independently of its spawner; when the spawner sleeps or stops,
        the member keeps running and is reclaimed by its own idle policy.

        Args:
            prototype: prototype directory name under ~/.tlon/prototypes/
            name: display name of the new member
            spawner_id: id of the agent requesting the spawn (audit + depth)
            session_id: session the spawn belongs to (quota bucket); None
                buckets all context-less spawns of the spawner together

        Returns:
            The new member's AgentConfig (with lineage fields).

        Raises:
            SpawnRejectedError: a budget cap was hit (see ``.reason``)
            PrototypeError: prototype missing, or the spawner declares no
                model for the child to inherit (ADR 0016)
        """
        registry = self._registry
        tlon_paths = registry._paths

        # Trust gate (stage 4a): before any budget check, an untrusted
        # prototype (LLM-authored, awaiting human approval) cannot spawn.
        # A missing prototype falls through to instantiate's not-found error.
        info = get_prototype_info(prototype, tlon_paths)
        if info is not None and not info.trusted:
            self._reject_spawn(
                "untrusted_prototype",
                spawner_id=spawner_id,
                session_id=session_id,
                prototype=prototype,
                detail=f"prototype '{prototype}' is untrusted (staged, awaiting approval)",
            )

        # Solvency gate (ADR 0014): an insolvent bloodline (long-term token
        # spend not covered by endorsed output) cannot spawn until it
        # earns endorsements back — the binary counterpart of the tilt.
        if not await self._solvent(prototype):
            self._reject_spawn(
                "insolvent_lineage",
                spawner_id=spawner_id,
                session_id=session_id,
                prototype=prototype,
                detail=(
                    f"bloodline '{prototype}' is insolvent (token spend not"
                    " covered by endorsed output); proposal/spawn eligibility"
                    " suspended until the bloodline earns endorsements"
                ),
            )

        # Budget 1: global activation cap (boot members included)
        if len(registry._agents) >= self._max_activations:
            self._reject_spawn(
                "max_activations",
                spawner_id=spawner_id,
                session_id=session_id,
                prototype=prototype,
                detail=f"running agents {len(registry._agents)} >= cap {self._max_activations}",
            )

        # Budget 2: spawn-chain depth (boot members are depth 0)
        spawner_config = registry._agent_info.get_config(spawner_id)
        spawner_depth = spawner_config.spawn_depth if spawner_config else 0
        depth = spawner_depth + 1
        if depth > self._max_spawn_depth:
            self._reject_spawn(
                "max_spawn_depth",
                spawner_id=spawner_id,
                session_id=session_id,
                prototype=prototype,
                detail=f"depth {depth} > cap {self._max_spawn_depth}",
            )

        # Budget 3: per-(spawner, session) quota, tilted by the target
        # bloodline's efficiency (ADR 0014): effective quota =
        # ceil(base * tilt), tilt clamped to [0.5, 2.0] upstream. Tilt
        # 1.0 (cold start / failure fallback) is the untilted behavior.
        # Probation cap (trust ladder, docs/design/unified-member-model.md):
        # a probationary *spawner* (spawned, not yet endorsed) is capped
        # at 1 spawn per session regardless of the tilt — tilt judges the
        # target bloodline, probation judges the spawner.
        tilt = await self._tilt(prototype)
        effective_quota = math.ceil(self._session_spawn_quota * tilt)
        probationary = await self._probationary(spawner_id)
        if probationary:
            effective_quota = min(effective_quota, 1)
        quota_key = (spawner_id, session_id or _UNKNOWN_SESSION)
        used = self._quota.get(quota_key, 0)
        if used >= effective_quota:
            probation_note = "; spawner on probation, capped at 1" if probationary else ""
            self._reject_spawn(
                "session_quota",
                spawner_id=spawner_id,
                session_id=session_id,
                prototype=prototype,
                detail=(
                    f"quota {effective_quota} per session exhausted"
                    f" (base {self._session_spawn_quota} × tilt {tilt:.2f})"
                    f"{probation_note}"
                ),
            )

        # Id is generated here (never caller-supplied): sanitize the
        # prototype fragment so the id stays within the AgentConfig charset
        safe_prototype = "".join(c for c in prototype if c.isalnum() or c in "-_")
        agent_id = f"c-{safe_prototype}-{uuid.uuid4().hex[:8]}"

        # Writes persona + behavior + agent.yaml (copy semantics; behavior
        # frozen at creation time). PrototypeError propagates to the caller.
        # Model inheritance (ADR 0016): the child runs the spawner's model —
        # the cost/capability profile follows the bloodline (ADR 0014's
        # selection-pressure semantics). A spawner without a declared model
        # fails here (no process-level fallback exists anymore).
        parent_model = (
            spawner_config.llm.model_name
            if spawner_config is not None and spawner_config.llm is not None
            else None
        )
        config = instantiate(
            prototype,
            agent_id,
            name,
            model=parent_model or "",
            params=params,
            tlon_paths=tlon_paths,
        )

        # Lineage audit (ADR 0006): who spawned this member and how deep.
        # No role is stamped — a spawned member is a member like any other.
        # Re-save so the YAML on disk carries the final config.
        config.spawned_by = spawner_id
        config.spawn_depth = depth
        yaml_save(config, tlon_paths.tlon_home())

        # Make the config visible to the provider (router, status queries)
        if not await registry._agent_info.reload_agent(agent_id):
            self._reject_spawn(
                "config_reload_failed",
                spawner_id=spawner_id,
                session_id=session_id,
                prototype=prototype,
                detail=f"provider could not load new config for '{agent_id}'",
            )

        self._quota[quota_key] = used + 1
        self._save_ledger()
        registry.register(agent_id)
        agent = await registry._start_agent(agent_id)

        logger.info(
            "agent_spawned",
            agent_id=agent_id,
            agent_name=name,
            prototype=prototype,
            spawner_id=spawner_id,
            spawn_depth=depth,
            started=agent is not None,
        )
        await registry.event_bus.publish(
            Event(
                type=EventType.AGENT_SPAWNED,
                payload={
                    "agent_id": agent_id,
                    "name": name,
                    "prototype": prototype,
                    "spawner_id": spawner_id,
                    "spawn_depth": depth,
                    "started": agent is not None,
                },
                source="agent_registry",
            )
        )
        return config

    def _reject_spawn(
        self,
        reason: str,
        *,
        spawner_id: str,
        session_id: str | None,
        prototype: str,
        detail: str,
    ) -> NoReturn:
        """Log and raise a budget rejection (single funnel for audit)."""
        logger.warning(
            "spawn_rejected",
            reason=reason,
            spawner_id=spawner_id,
            session_id=session_id,
            prototype=prototype,
            detail=detail,
        )
        raise SpawnRejectedError(reason, f"spawn rejected ({reason}): {detail}")

    async def adopt_agent(self, agent_id: str) -> str:
        """Adopt an agent created out-of-process (wizard/mirror), no restart.

        External tools write ``agent.yaml`` directly; this makes the running
        server pick the new agent up: the provider reloads the config, the
        agent is registered, and non-lazy agents are started immediately
        (same assembly path as boot/spawn). A lazy agent is registered only
        and wakes on its first mail, same as at boot.

        Args:
            agent_id: id of the agent to adopt

        Returns:
            One of "already-known" (running or registered — not (re)started
            here, but the config IS reloaded from disk: the agent.yaml may
            have been edited out-of-process, e.g. a WebUI member edit, and
            the next start must assemble the fresh config rather than the
            stale provider cache), "config-not-found" (no agent.yaml on
            disk), "registered-lazy", "started", "start-failed"
            (``agent_start_failed`` already logged by ``_start_agent``).
        """
        registry = self._registry
        if agent_id in registry._agents or agent_id in registry._registered_ids:
            await registry._agent_info.reload_agent(agent_id)
            registry._profiles.pop(agent_id, None)  # spec may have changed (phase E)
            result = "already-known"
        elif not await registry._agent_info.reload_agent(agent_id):
            result = "config-not-found"
        else:
            registry.register(agent_id)
            registry._profiles.pop(agent_id, None)  # spec may have changed (phase E)
            config = registry._agent_info.get_config(agent_id)
            if config is not None and registry.is_lazy(agent_id):
                result = "registered-lazy"
            else:
                agent = await registry._start_agent(agent_id)
                result = "started" if agent is not None else "start-failed"

        logger.info("agent_adopted", agent_id=agent_id, result=result)
        return result
