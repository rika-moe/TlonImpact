"""Society-level container resource ledger (issue #24, constitution 4).

Makes the society's actual resource consumption queryable data: a
background task periodically enumerates ``tlon-agent-*`` containers and
collects real memory/CPU usage from the docker daemon. The latest
snapshot is exposed through ``stats_snapshot()`` — a plain in-memory
read — which ``MetricsService`` (base layer) consumes as observable
gauges, so ``GET /metrics`` answers "how much is the society spending
right now" without ever blocking the synchronous snapshot path on a
docker call.

Fail-soft by design: an unreachable daemon (client creation failure or
a collect error) logs a warning and yields an empty snapshot; the
background task survives and retries on the next cycle. The host can
always run tlon without docker (e.g. tests, mail-only usage) — the
ledger simply reports nothing.

Budget is warning-only: when ``TLON_CONTAINER_MEMORY_BUDGET`` is set and
the society's total container memory exceeds it, a
``container_memory_budget_exceeded`` warning is logged. No enforcement
(activation refusal etc.) — that policy is a separate decision.
"""

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, cast

import docker
from docker.utils import parse_bytes

from tlon.base.component import Component
from tlon.base.execution import bind_execution
from tlon.base.logger import get_logger
from tlon.sandbox.docker_client import create_docker_client
from tlon.sandbox.docker_runtime import EXEC_NAME_MARKER

logger = get_logger(__name__)

#: Agent containers are named ``tlon-agent-{agent_id}`` (the file cabinet)
#: and ephemeral exec units ``tlon-agent-{agent_id}-exec-{uuid8}``
#: (docker_runtime.py, issue #25). Cabinets never start (they serve file
#: I/O only), so only exec units ever appear in this usage ledger.
CONTAINER_NAME_PREFIX = "tlon-agent-"


@dataclass(frozen=True)
class ContainerUsage:
    """One container's resource usage at one collect cycle."""

    agent_id: str
    memory_bytes: int
    memory_limit_bytes: int
    cpu_percent: float


def _cpu_percent(stats: dict[str, Any]) -> float:
    """Standard docker-stats CPU formula over one stats payload.

    ``(cpu_delta / system_delta) * online_cpus * 100`` where the deltas
    are between ``cpu_stats`` and ``precpu_stats``. ``online_cpus`` falls
    back to ``len(percpu_usage)``. A non-positive ``system_delta`` (first
    sample / counter boundary) safely yields 0.0.
    """
    cpu_stats: dict[str, Any] = stats.get("cpu_stats") or {}
    precpu_stats: dict[str, Any] = stats.get("precpu_stats") or {}
    cpu_usage: dict[str, Any] = cpu_stats.get("cpu_usage") or {}
    precpu_usage: dict[str, Any] = precpu_stats.get("cpu_usage") or {}
    cpu_delta = (cpu_usage.get("total_usage") or 0) - (precpu_usage.get("total_usage") or 0)
    system_delta = (cpu_stats.get("system_cpu_usage") or 0) - (
        precpu_stats.get("system_cpu_usage") or 0
    )
    if system_delta <= 0:
        return 0.0
    online_cpus = cpu_stats.get("online_cpus")
    if online_cpus is None:
        online_cpus = len(cpu_usage.get("percpu_usage") or []) or 1
    return (cpu_delta / system_delta) * online_cpus * 100.0


def _container_usage(container_name: str, stats: dict[str, Any]) -> ContainerUsage:
    """Extract one ContainerUsage from a docker stats payload.

    Ephemeral exec units (``tlon-agent-{agent_id}-exec-{uuid8}``, issue
    #25) are attributed to their owning member — without the marker cut,
    every command would spawn a fresh metric series with a random uuid
    label.
    """
    memory_stats: dict[str, Any] = stats.get("memory_stats") or {}
    agent_id = container_name.removeprefix(CONTAINER_NAME_PREFIX).partition(EXEC_NAME_MARKER)[0]
    return ContainerUsage(
        agent_id=agent_id,
        memory_bytes=memory_stats.get("usage") or 0,
        memory_limit_bytes=memory_stats.get("limit") or 0,
        cpu_percent=_cpu_percent(stats),
    )


class ContainerResourceMonitor(Component):
    """Periodically collects real resource usage of all agent containers.

    Lifecycle (Component discipline): the constructor only stores
    configuration; ``initialize()`` creates the docker client (shared
    probe chain, ``create_docker_client``) and starts the background
    collect loop; ``shutdown()`` cancels the loop and closes the client.

    All docker SDK calls run via ``asyncio.to_thread`` so the event loop
    never blocks on the daemon.
    """

    def __init__(self, interval: float = 30.0, memory_budget: str = ""):
        """Initialize the monitor.

        Args:
            interval: Seconds between collect cycles.
            memory_budget: Society-level container memory budget (e.g.
                "4g", parsed via ``docker.utils.parse_bytes``); empty
                means no budget. Exceeding it logs a warning only —
                no enforcement.
        """
        super().__init__(name="ContainerResourceMonitor")
        self._interval = interval
        self._budget_bytes: int | None = None
        if memory_budget:
            try:
                self._budget_bytes = int(parse_bytes(memory_budget))
            except Exception as e:
                logger.warning(
                    "container_memory_budget_invalid", budget=memory_budget, error=str(e)
                )
        self._client: docker.DockerClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._latest: dict[str, ContainerUsage] = {}

    async def initialize(self) -> None:
        """Create the docker client and start the background collect loop.

        An unreachable daemon is not fatal: the client is left as None
        and every collect cycle yields an empty snapshot (fail-soft).
        """
        try:
            self._client = await asyncio.to_thread(create_docker_client)
        except Exception as e:
            logger.warning("container_monitor_docker_unreachable", error=str(e))
            self._client = None
        with bind_execution(None):
            self._task = asyncio.create_task(self._run())
        self._initialized = True
        logger.info("container_resource_monitor_initialized", interval=self._interval)

    async def shutdown(self) -> None:
        """Stop the collect loop and close the docker client."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
            self._client = None
        self._initialized = False
        logger.info("container_resource_monitor_shutdown")

    def stats_snapshot(self) -> dict[str, dict[str, float]]:
        """Latest usage as a plain structured dict (agent_id -> fields).

        Pure in-memory read of the last successful collect — safe to call
        from the synchronous ``MetricsService.snapshot()`` path. The dict
        shape is the port base/metrics.py consumes; the base layer never
        sees ``ContainerUsage`` itself.
        """
        return {
            agent_id: {
                "memory_bytes": float(usage.memory_bytes),
                "memory_limit_bytes": float(usage.memory_limit_bytes),
                "cpu_percent": usage.cpu_percent,
            }
            for agent_id, usage in self._latest.items()
        }

    async def _run(self) -> None:
        """Background loop: collect, then sleep one interval."""
        while True:
            self._latest = await self._collect()
            await asyncio.sleep(self._interval)

    async def _collect(self) -> dict[str, ContainerUsage]:
        """One collect cycle over all ``tlon-agent-*`` containers.

        Any daemon error logs a warning and yields an empty snapshot —
        the loop retries next cycle.
        """
        if self._client is None:
            return {}
        try:
            containers = await asyncio.to_thread(
                self._client.containers.list, filters={"name": CONTAINER_NAME_PREFIX}
            )
            usage: dict[str, ContainerUsage] = {}
            for container in containers:
                name = container.name
                if not name:
                    continue
                stats = cast(
                    dict[str, Any],
                    await asyncio.to_thread(container.stats, stream=False),
                )
                entry = _container_usage(name, stats)
                # An agent can have several exec units running at once
                # (issue #25); aggregate their usage under the owning
                # agent_id (limits are identical per agent, so the
                # latest wins instead of being summed).
                prev = usage.get(entry.agent_id)
                usage[entry.agent_id] = (
                    entry
                    if prev is None
                    else ContainerUsage(
                        agent_id=entry.agent_id,
                        memory_bytes=prev.memory_bytes + entry.memory_bytes,
                        memory_limit_bytes=entry.memory_limit_bytes,
                        cpu_percent=prev.cpu_percent + entry.cpu_percent,
                    )
                )
            self._check_budget(usage)
            return usage
        except Exception as e:
            logger.warning("container_resource_collect_failed", error=str(e))
            return {}

    def _check_budget(self, usage: dict[str, ContainerUsage]) -> None:
        """Warn when the society's total container memory exceeds the budget.

        Warning-only (issue #24): no enforcement is applied here.
        """
        if self._budget_bytes is None:
            return
        total = sum(u.memory_bytes for u in usage.values())
        if total > self._budget_bytes:
            logger.warning(
                "container_memory_budget_exceeded",
                total_bytes=total,
                budget_bytes=self._budget_bytes,
                container_count=len(usage),
            )
