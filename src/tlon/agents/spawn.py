"""Spawn budget and derivation logic (split from agent_registry, ).

``SpawnManager`` owns the stage-6b spawn budget (global activation cap,
spawn depth, per-session quota) and executes the two member-derivation
flows: ``spawn_member`` (prototype instantiation under the budget) and
``adopt_agent`` (hot adoption of an out-of-process creation).

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
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from tlon.agents.prototypes import get_prototype_info, instantiate
from tlon.base.config import AgentConfig, yaml_save
from tlon.base.events.event import Event, EventType
from tlon.base.logger import get_logger
from tlon.base.paths import paths
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
    ) -> None:
        self._registry = registry
        self._max_activations = max_activations
        self._max_spawn_depth = max_spawn_depth
        self._session_spawn_quota = session_spawn_quota
        # (spawner_id, session_id) -> successful spawn count, persisted to
        # disk so the anti-runaway quota survives restarts (sessions do,
        # via journal replay).
        self._ledger_path: Path = (registry._paths or paths).spawn_quota_ledger_file()
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
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, self._ledger_path)
        except OSError as e:
            logger.error(
                "spawn_quota_ledger_save_failed", file_path=str(self._ledger_path), error=str(e)
            )

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
            PrototypeError: prototype missing
        """
        registry = self._registry
        tlon_paths = registry._paths or paths

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

        # Budget 3: per-(spawner, session) quota
        quota_key = (spawner_id, session_id or _UNKNOWN_SESSION)
        used = self._quota.get(quota_key, 0)
        if used >= self._session_spawn_quota:
            self._reject_spawn(
                "session_quota",
                spawner_id=spawner_id,
                session_id=session_id,
                prototype=prototype,
                detail=f"quota {self._session_spawn_quota} per session exhausted",
            )

        # Id is generated here (never caller-supplied): sanitize the
        # prototype fragment so the id stays within the AgentConfig charset
        safe_prototype = "".join(c for c in prototype if c.isalnum() or c in "-_")
        agent_id = f"c-{safe_prototype}-{uuid.uuid4().hex[:8]}"

        # Writes persona + behavior + agent.yaml (copy semantics; behavior
        # frozen at creation time). PrototypeError propagates to the caller.
        config = instantiate(prototype, agent_id, name, params=params, tlon_paths=tlon_paths)

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
            One of "already-known" (running or registered — no-op; a known
            agent's config is never reloaded here), "config-not-found" (no
            agent.yaml on disk), "registered-lazy", "started", "start-failed"
            (``agent_start_failed`` already logged by ``_start_agent``).
        """
        registry = self._registry
        if agent_id in registry._agents or agent_id in registry._registered_ids:
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
