"""Tests for the society-level container resource ledger
(tlon.sandbox.resource_monitor, issue #24).

Covers:
a) The docker-stats cpu_percent formula, incl. the system_delta <= 0
   boundary and the online_cpus fallbacks.
b) agent_id extraction from container names.
c) One successful collect cycle over a mocked docker client.
d) Fail-soft: unreachable daemon / collect errors yield {} and never
   kill the component.
e) Warning-only memory budget (structlog capture_logs, repo style).
f) Component lifecycle: initialize starts the loop, shutdown cancels it
   and closes the client.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

from tlon.sandbox import resource_monitor
from tlon.sandbox.resource_monitor import (
    ContainerResourceMonitor,
    ContainerUsage,
    _container_usage,
    _cpu_percent,
)


def _stats(
    cpu_total: int = 0,
    precpu_total: int = 0,
    system: int = 0,
    presystem: int = 0,
    online_cpus: int | None = 4,
    percpu: list[int] | None = None,
    mem_usage: int = 0,
    mem_limit: int = 0,
) -> dict[str, Any]:
    """Build a docker stats (stream=False) payload."""
    cpu_usage: dict[str, Any] = {"total_usage": cpu_total}
    if percpu is not None:
        cpu_usage["percpu_usage"] = percpu
    cpu_stats: dict[str, Any] = {"cpu_usage": cpu_usage, "system_cpu_usage": system}
    if online_cpus is not None:
        cpu_stats["online_cpus"] = online_cpus
    return {
        "cpu_stats": cpu_stats,
        "precpu_stats": {
            "cpu_usage": {"total_usage": precpu_total},
            "system_cpu_usage": presystem,
        },
        "memory_stats": {"usage": mem_usage, "limit": mem_limit},
    }


def _client_with(containers: list[MagicMock]) -> MagicMock:
    client = MagicMock()
    client.containers.list.return_value = containers
    return client


def _container(name: str, stats: dict[str, Any]) -> MagicMock:
    container = MagicMock()
    container.name = name
    container.stats.return_value = stats
    return container


# ── cpu_percent formula ──


def test_cpu_percent_standard_formula() -> None:
    """(cpu_delta / system_delta) * online_cpus * 100."""
    stats = _stats(cpu_total=1200, precpu_total=1000, system=5000, presystem=4000, online_cpus=4)
    # cpu_delta=200, system_delta=1000 → 0.2 * 4 * 100
    assert _cpu_percent(stats) == pytest.approx(80.0)


def test_cpu_percent_zero_when_system_delta_non_positive() -> None:
    """First sample / counter boundary: system_delta <= 0 → 0.0 (no div-by-zero)."""
    assert _cpu_percent(_stats(cpu_total=100, system=4000, presystem=4000)) == 0.0
    assert _cpu_percent(_stats(cpu_total=100, system=3000, presystem=4000)) == 0.0
    # Missing stats entirely (empty payload) also degrades to 0.0
    assert _cpu_percent({}) == 0.0


def test_cpu_percent_online_cpus_falls_back_to_percpu_len() -> None:
    """Without online_cpus, the CPU count comes from len(percpu_usage)."""
    stats = _stats(
        cpu_total=1100,
        precpu_total=1000,
        system=5000,
        presystem=4000,
        online_cpus=None,
        percpu=[50, 50],
    )
    # cpu_delta=100, system_delta=1000 → 0.1 * 2 * 100
    assert _cpu_percent(stats) == pytest.approx(20.0)


def test_cpu_percent_no_cpu_count_defaults_to_one() -> None:
    """Neither online_cpus nor percpu_usage → single-CPU fallback."""
    stats = _stats(
        cpu_total=1500, precpu_total=1000, system=5000, presystem=4000, online_cpus=None
    )
    assert _cpu_percent(stats) == pytest.approx(50.0)


# ── ContainerUsage extraction ──


def test_container_usage_strips_agent_id_and_reads_memory() -> None:
    """agent_id is the container name minus the tlon-agent- prefix."""
    stats = _stats(mem_usage=123, mem_limit=456)
    usage = _container_usage("tlon-agent-gu-yanxi", stats)
    assert usage == ContainerUsage(
        agent_id="gu-yanxi", memory_bytes=123, memory_limit_bytes=456, cpu_percent=0.0
    )


def test_container_usage_missing_memory_stats_defaults_zero() -> None:
    usage = _container_usage("tlon-agent-a", {})
    assert usage.memory_bytes == 0
    assert usage.memory_limit_bytes == 0


def test_exec_unit_name_is_attributed_to_owning_agent() -> None:
    """Ephemeral exec units (issue #25) roll up under their member —
    no per-command uuid metric series."""
    usage = _container_usage("tlon-agent-gu-yanxi-exec-a1b2c3d4", _stats(mem_usage=64))
    assert usage.agent_id == "gu-yanxi"
    assert usage.memory_bytes == 64


async def test_collect_aggregates_concurrent_exec_units_of_one_agent() -> None:
    """Several exec units of the same agent sum usage under one key."""
    client = _client_with(
        [
            _container("tlon-agent-a-exec-11111111", _stats(mem_usage=100, mem_limit=1024)),
            _container("tlon-agent-a-exec-22222222", _stats(mem_usage=50, mem_limit=1024)),
        ]
    )
    monitor = ContainerResourceMonitor()
    monitor._client = client

    usage = await monitor._collect()

    assert set(usage) == {"a"}
    assert usage["a"].memory_bytes == 150
    assert usage["a"].memory_limit_bytes == 1024


# ── Collect cycle ──


async def test_collect_lists_agent_containers_and_aggregates() -> None:
    """One cycle: list tlon-agent-* containers, stats each, key by agent_id."""
    client = _client_with(
        [
            _container("tlon-agent-gu-yanxi", _stats(mem_usage=100, mem_limit=1024)),
            _container("tlon-agent-xie-yunshu", _stats(mem_usage=200, mem_limit=1024)),
        ]
    )
    monitor = ContainerResourceMonitor()
    monitor._client = client

    usage = await monitor._collect()

    client.containers.list.assert_called_once_with(filters={"name": "tlon-agent-"})
    assert set(usage) == {"gu-yanxi", "xie-yunshu"}
    assert usage["gu-yanxi"].memory_bytes == 100
    assert usage["xie-yunshu"].memory_bytes == 200


async def test_collect_failure_returns_empty_and_warns() -> None:
    """A daemon error mid-collect logs a warning and yields {} (loop retries)."""
    client = MagicMock()
    client.containers.list.side_effect = RuntimeError("daemon gone")
    monitor = ContainerResourceMonitor()
    monitor._client = client

    with capture_logs() as logs:
        usage = await monitor._collect()

    assert usage == {}
    assert any(entry["event"] == "container_resource_collect_failed" for entry in logs)


def test_stats_snapshot_shape() -> None:
    """stats_snapshot exposes the plain-dict port consumed by MetricsService."""
    monitor = ContainerResourceMonitor()
    monitor._latest = {"gu-yanxi": ContainerUsage("gu-yanxi", 100, 200, 55.5)}
    assert monitor.stats_snapshot() == {
        "gu-yanxi": {"memory_bytes": 100.0, "memory_limit_bytes": 200.0, "cpu_percent": 55.5}
    }


# ── Lifecycle / fail-soft ──


async def test_initialize_failsoft_when_daemon_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Client creation failure: warning, empty ledger, component still usable."""

    def _boom() -> None:
        raise RuntimeError("no daemon")

    monkeypatch.setattr(resource_monitor, "create_docker_client", _boom)
    monitor = ContainerResourceMonitor(interval=999)

    with capture_logs() as logs:
        await monitor.initialize()
    try:
        assert monitor._client is None
        assert await monitor._collect() == {}
        assert monitor.stats_snapshot() == {}
    finally:
        await monitor.shutdown()
    assert any(entry["event"] == "container_monitor_docker_unreachable" for entry in logs)


async def test_shutdown_cancels_loop_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client_with([])
    monkeypatch.setattr(resource_monitor, "create_docker_client", lambda: client)
    monitor = ContainerResourceMonitor(interval=999)
    await monitor.initialize()
    task = monitor._task
    assert task is not None and not task.done()

    await monitor.shutdown()

    assert task.done()
    client.close.assert_called_once_with()
    assert monitor._client is None
    assert monitor._task is None


# ── Warning-only budget ──


async def test_budget_exceeded_warns() -> None:
    """Total container memory over the budget logs container_memory_budget_exceeded."""
    client = _client_with(
        [
            _container("tlon-agent-a", _stats(mem_usage=700 * 1024)),
            _container("tlon-agent-b", _stats(mem_usage=700 * 1024)),
        ]
    )
    monitor = ContainerResourceMonitor(memory_budget="1m")  # 1 MiB
    monitor._client = client

    with capture_logs() as logs:
        await monitor._collect()

    exceeded = [e for e in logs if e["event"] == "container_memory_budget_exceeded"]
    assert len(exceeded) == 1
    assert exceeded[0]["total_bytes"] == 1400 * 1024
    assert exceeded[0]["budget_bytes"] == 1024 * 1024


async def test_budget_not_exceeded_is_quiet() -> None:
    client = _client_with([_container("tlon-agent-a", _stats(mem_usage=100))])
    monitor = ContainerResourceMonitor(memory_budget="1m")
    monitor._client = client

    with capture_logs() as logs:
        await monitor._collect()

    assert not any(e["event"] == "container_memory_budget_exceeded" for e in logs)


def test_invalid_budget_warns_and_disables_budget() -> None:
    """An unparsable budget logs a warning and behaves like 'no budget'."""
    with capture_logs() as logs:
        monitor = ContainerResourceMonitor(memory_budget="bogus")
    assert monitor._budget_bytes is None
    assert any(e["event"] == "container_memory_budget_invalid" for e in logs)
