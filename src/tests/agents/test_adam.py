"""Adam — the policy member (PID 1), now a unified MemberRuntime instance.

Pins Adam's two lifecycle policy lines as data: boot (start-all for members
whose spec declares ``:on :boot``, phase E spec-derived) and drain
(idle-fact → stop request). Wake is no longer Adam's line — phase E moved
it into the registry as a spec-derived mechanized policy (see
``test_registry.py``). The registry is stubbed with its read-only
interfaces (``get_agent`` / ``get_config`` / ``get_all`` /
``boot_candidates`` / ``is_lazy``).
"""

import asyncio

import pytest

from tlon.agents.adam import make_adam
from tlon.base.config import AgentConfig
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus


class _RegistryStub:
    """The registry's read-only query surface Adam is injected with."""

    def __init__(
        self,
        configs: dict[str, AgentConfig],
        running: set[str] | None = None,
        boot_eligible: set[str] | None = None,
    ):
        self._configs = configs
        self._running = running or set()
        self._boot_eligible = boot_eligible or set()

    def get_agent(self, agent_id: str):
        return "running" if agent_id in self._running else None

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)

    def get_all(self) -> list[str]:
        return list(self._configs)

    def boot_candidates(self) -> list[str]:
        """The boot set: eligible and not already running."""
        return [
            aid for aid in self._configs if aid in self._boot_eligible and aid not in self._running
        ]

    def is_lazy(self, agent_id: str) -> bool:
        """Lazy = not in the boot set (phase E spec-derived shape)."""
        return agent_id not in self._boot_eligible


@pytest.fixture
async def bus():
    b = EventBus()
    await b.initialize()
    yield b
    await b.shutdown()


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out waiting for condition")


def _worker(id: str = "worker-1") -> AgentConfig:
    return AgentConfig(id=id, name=id)


class TestAdamDecisions:
    async def test_boot_start_all(self, bus):
        registry = _RegistryStub(
            {"worker-1": _worker(), "lazy-1": _worker("lazy-1")},
            boot_eligible={"worker-1"},
        )

        adam = make_adam(bus, registry)
        starts: list[Event] = []
        bus.subscribe(EventType.AGENT_START_REQUEST, lambda e: starts.append(e))

        await adam.initialize()  # the :boot rule runs start-all here
        await _wait_for(lambda: len(starts) == 1)
        assert starts[0].payload.agent_id == "worker-1"
        assert starts[0].payload.reason == "boot"

        await adam.shutdown()

    async def test_drain_idle(self, bus):
        registry = _RegistryStub({"worker-1": _worker()}, running={"worker-1"})
        adam = make_adam(bus, registry)
        stops: list[Event] = []
        bus.subscribe(EventType.AGENT_STOP_REQUEST, lambda e: stops.append(e))
        await adam.initialize()

        await bus.publish(
            Event(type=EventType.AGENT_IDLE, payload={"agent_id": "worker-1"}, source="registry")
        )
        await _wait_for(lambda: len(stops) == 1)
        assert stops[0].payload.agent_id == "worker-1"
        assert stops[0].payload.reason == "drain"

        await adam.shutdown()
