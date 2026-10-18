"""Adam — the policy member (PID 1), now a unified MemberRuntime instance.

Pins Adam's five policy lines as data: boot (start-all for members
whose spec declares ``:on :boot``), drain
(idle-fact → stop request), noisy-circuit-breaker (AGENT_NOISY → stop
the flooding member), welcome (AGENT_SPAWNED
triggers a birth-socialization mail to the newborn) and guardian-inbox
(a fixed acknowledgement reply to mail addressed to Adam,
postmaster senders excluded). Wake is no longer
Adam's line — wake-on-mail lives in the registry as a spec-derived
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
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.post_office import PostOffice
from tlon.organization.mail.resolver import RecipientResolver


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
        # RegistryQueries.member_roster no longer touches paths (the
        # yellow page carries no prototype metadata); None here is never
        # hit either way.
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


def _make_adam(bus, registry, paths):
    office = PostOffice(bus, RecipientResolver(registry, paths), paths)
    return make_adam(bus, registry, paths, post_office=office)


@pytest.fixture
async def bus():
    b = EventBus()
    await b.initialize()
    yield b
    await b.shutdown()


@pytest.fixture
def tlon_paths(tmp_path) -> TlonPaths:
    """Isolated tlon home — Adam's mailbox must not touch the real one."""
    return TlonPaths(home=tmp_path)


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
    async def test_boot_start_all(self, bus, tlon_paths):
        registry = _RegistryStub(
            {"worker-1": _worker(), "lazy-1": _worker("lazy-1")},
            boot_eligible={"worker-1"},
        )

        adam = _make_adam(bus, registry, tlon_paths)
        starts: list[Event] = []
        bus.subscribe(EventType.AGENT_START_REQUEST, lambda e: starts.append(e))

        await adam.initialize()  # the :boot rule runs start-all here
        await _wait_for(lambda: len(starts) == 1)
        assert starts[0].payload.agent_id == "worker-1"
        assert starts[0].payload.reason == "boot"

        await adam.shutdown()

    async def test_drain_idle(self, bus, tlon_paths):
        registry = _RegistryStub({"worker-1": _worker()}, running={"worker-1"})
        adam = _make_adam(bus, registry, tlon_paths)
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

    async def test_noisy_circuit_breaker(self, bus, tlon_paths):
        """AGENT_NOISY (PublicHealthMonitor's fact) → stop the flooding member."""
        registry = _RegistryStub({"worker-1": _worker()}, running={"worker-1"})
        adam = _make_adam(bus, registry, tlon_paths)
        stops: list[Event] = []
        bus.subscribe(EventType.AGENT_STOP_REQUEST, lambda e: stops.append(e))
        await adam.initialize()

        await bus.publish(
            Event(
                type=EventType.AGENT_NOISY,
                payload={
                    "agent_id": "worker-1",
                    "window_seconds": 60.0,
                    "mail_count": 25,
                },
                source="public_health_monitor",
            )
        )
        await _wait_for(lambda: len(stops) == 1)
        assert stops[0].payload.agent_id == "worker-1"
        assert stops[0].payload.reason == "noisy-circuit-breaker"

        await adam.shutdown()

    async def test_noisy_fact_without_agent_id_is_skipped(self, bus, tlon_paths):
        """A malformed fact fails safe: the action raises, the rule is skipped."""
        registry = _RegistryStub({"worker-1": _worker()}, running={"worker-1"})
        adam = _make_adam(bus, registry, tlon_paths)
        stops: list[Event] = []
        bus.subscribe(EventType.AGENT_STOP_REQUEST, lambda e: stops.append(e))
        await adam.initialize()

        await bus.publish(
            Event(
                type=EventType.AGENT_NOISY,
                payload={"mail_count": 25},
                source="public_health_monitor",
            )
        )
        await asyncio.sleep(0.1)
        assert stops == []

        await adam.shutdown()

    async def test_welcome_newborn(self, bus, tlon_paths):
        """Issue : AGENT_SPAWNED triggers a welcome mail to the newborn.

        The recipient address is assembled in the spec (``str``) from the
        event's agent id — emit recipients are full mail addresses, never
        bare ids (``Mail.create`` rejects non-addresses).
        """
        registry = _RegistryStub(
            {"worker-1": _worker(), "c-xin-1a2b3c4d": _worker("c-xin-1a2b3c4d")},
            running={"worker-1"},
        )
        adam = _make_adam(bus, registry, tlon_paths)
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
        # Complete the producer before draining its emitted notification. The
        # welcome includes a real SQLite commit, whose latency is not a 2s SLA.
        # Keep a bound for hangs, then assert the actual behavior after completion.
        async with asyncio.timeout(10):
            for event_type in (EventType.AGENT_SPAWNED, EventType.MAIL_NEW):
                await asyncio.gather(
                    *(subscription.queue.join() for subscription in bus._subscribers[event_type])
                )
        assert len(mails) == 1
        mail = mails[0].payload
        assert mail.from_address == "adam@tlon.internal"
        assert mail.to == ["c-xin-1a2b3c4d@tlon.internal"]
        # Greeting names the newborn and points it at its own discovery
        # channels (its contact book, the listMembers yellow page); the
        # roster is no longer embedded (docs/design/mail.md).
        assert "小新" in mail.content
        assert "worker-1（worker-1）" not in mail.content
        assert "updateContact" in mail.content
        assert "listMembers" in mail.content
        assert "adam@tlon.internal" in mail.content

        await adam.shutdown()


class TestGuardianInbox:
    """Issue : mail addressed to Adam gets a fixed acknowledgement.

    The guardian-inbox rule is deterministic (``:on :mail-delivered``, no
    ``:loop``); its ``:when`` gate restricts replies to mail addressed to
    Adam and never answers postmaster (a bounce must not be bounced back).
    """

    def _mail_to_adam(self, from_address: str = "worker-1@tlon.internal", **kwargs) -> Mail:
        return Mail.create(
            from_address=from_address,
            to=["adam@tlon.internal"],
            content="守护者，我有话说",
            **kwargs,
        )

    async def test_mail_to_adam_gets_acknowledgement_reply(
        self, bus: EventBus, tlon_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = _RegistryStub({"worker-1": _worker()}, running={"worker-1"})
        adam = _make_adam(bus, registry, tlon_paths)
        mails: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: mails.append(e))
        await adam.initialize()

        assert adam._mailbox is not None
        mark_read = adam._mailbox.mark_read
        allow_mark_read = asyncio.Event()
        marked_read = asyncio.Event()

        async def controlled_mark_read(mail_id: str) -> None:
            await allow_mark_read.wait()
            await mark_read(mail_id)
            marked_read.set()

        monkeypatch.setattr(adam._mailbox, "mark_read", controlled_mark_read)

        incoming = self._mail_to_adam()
        # Simulate delivery: the mail sits unread in Adam's inbox, then
        # the MAIL_DELIVERED event arrives (docs/design/mail.md: 落盘先于通知).
        inbox = Mailbox(tlon_paths.mail_database_file(), "adam")
        await inbox.append_inbox(incoming)
        assert [m.id for m in await inbox.unread()] == [incoming.id]
        await bus.publish(
            Event(
                type=EventType.MAIL_DELIVERED,
                payload={"mail": incoming, "recipient": "adam"},
                source="mail_delivery",
            )
        )
        await _wait_for(lambda: len(mails) == 1)
        reply = mails[0].payload
        assert reply.from_address == "adam@tlon.internal"
        assert reply.to == ["worker-1@tlon.internal"]  # replies to the sender
        assert reply.in_reply_to == incoming.id  # threading inherited
        assert reply.thread_id == incoming.thread_id
        assert "收讫" in reply.content

        # Reply acceptance precedes the asynchronous read mark. A reply
        # notification cannot serve as the completion barrier for that write.
        assert [m.id for m in await inbox.unread()] == [incoming.id]
        allow_mark_read.set()
        await asyncio.wait_for(marked_read.wait(), timeout=2)
        assert [m.id for m in await inbox.unread()] == []

        await adam.shutdown()

    async def test_mail_addressed_to_another_member_is_ignored(self, bus, tlon_paths):
        registry = _RegistryStub({"worker-1": _worker()}, running={"worker-1"})
        adam = _make_adam(bus, registry, tlon_paths)
        mails: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: mails.append(e))
        await adam.initialize()

        incoming = Mail.create(
            from_address="worker-1@tlon.internal",
            to=["a-002@tlon.internal"],
            content="不是给守护者的",
        )
        await bus.publish(
            Event(
                type=EventType.MAIL_DELIVERED,
                payload={"mail": incoming, "recipient": "a-002"},
                source="mail_delivery",
            )
        )
        await asyncio.sleep(0.1)
        assert mails == []

        await adam.shutdown()

    async def test_postmaster_mail_gets_no_reply(self, bus, tlon_paths):
        """A bounce landing in Adam's inbox must not be answered (loop guard)."""
        registry = _RegistryStub({"worker-1": _worker()}, running={"worker-1"})
        adam = _make_adam(bus, registry, tlon_paths)
        mails: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: mails.append(e))
        await adam.initialize()

        bounce = self._mail_to_adam(from_address="postmaster@tlon.internal")
        await bus.publish(
            Event(
                type=EventType.MAIL_DELIVERED,
                payload={"mail": bounce, "recipient": "adam"},
                source="mail_delivery",
            )
        )
        await asyncio.sleep(0.1)
        assert mails == []

        await adam.shutdown()
