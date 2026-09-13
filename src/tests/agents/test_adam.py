"""Adam — the policy member (PID 1), now a unified MemberRuntime instance.

Pins Adam's three policy lines as data: boot (start-all for members
whose spec declares ``:on :boot``, phase E spec-derived), drain
(idle-fact → stop request) and welcome (issue  — AGENT_SPAWNED
triggers a birth-socialization mail to the newborn). Wake is no longer
Adam's line — phase E moved it into the registry as a spec-derived
mechanized policy (see ``test_registry.py``). The registry is stubbed
with its read-only interfaces (``get_agent`` / ``get_config`` /
``get_all`` / ``get_all_ids`` / ``boot_candidates`` / ``is_lazy``).
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
        # RegistryQueries.member_roster reads ``_paths`` for prototype
        # metadata; None = the module singleton (never hit here — no stub
        # config carries a prototype).
        self._paths = None

    def get_agent(self, agent_id: str):
        return "running" if agent_id in self._running else None

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)

    def get_all(self) -> list[str]:
        return list(self._configs)

    def get_all_ids(self) -> list[str]:
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

    async def test_welcome_newborn(self, bus):
        """Issue : AGENT_SPAWNED triggers a welcome mail to the newborn.

        The recipient address is assembled in the spec (``str``) from the
        event's agent id — emit recipients are full mail addresses, never
        bare ids (``Mail.create`` rejects non-addresses).
        """
        registry = _RegistryStub({"worker-1": _worker()}, running={"worker-1"})
        adam = make_adam(bus, registry)
        mails: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: mails.append(e))
        await adam.initialize()

        await bus.publish(
            Event(
                type=EventType.AGENT_SPAWNED,
                payload={
                    "agent_id": "c-xin-1a2b3c4d",
                    "name": "小新",
                    "prototype": "worker",
                    "spawner_id": "worker-1",
                    "spawn_depth": 1,
                    "started": True,
                },
                source="agent_registry",
            )
        )
        await _wait_for(lambda: len(mails) == 1)
        mail = mails[0].payload
        assert mail.from_address == "adam@tlon.internal"
        assert mail.to == ["c-xin-1a2b3c4d@tlon.internal"]
        # Greeting names the newborn; the roster lists the society.
        assert "小新" in mail.content
        assert "worker-1（worker-1）" in mail.content

        await adam.shutdown()
