"""AgentRegistry — the C4 unified assembler (no runtime fork).

Pins the merged ``_start_agent``: one MemberRuntime for every member, the
derived ``runtime`` in the AGENT_STARTED payload, and the spec hash
provenance. A deterministic member (no loop rule) assembles with no LLM /
memory / Docker infrastructure — the C4 fork removal is the behavior under
test.
"""

import asyncio
import shutil

import pytest

from tlon.agents.agent_registry import AgentRegistry, RegistryQueries
from tlon.agents.lifecycle import StartRequest
from tlon.agents.loader import load_member_spec
from tlon.base.config import AgentConfig, AgentInfoProvider
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox

RELAY_EDN = """\
{:version 1
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["sink@tlon.internal"]})]}]}
"""

#: Mail rule + a boot rule: belongs to the boot set and subscribes to mail.
BOOTY_EDN = """\
{:version 1
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["sink@tlon.internal"]})]}
         {:name "boot"
          :on :boot
          :actions []}]}
"""

#: Boot rule only — no mail subscription (cannot be woken by mail).
BOOT_ONLY_EDN = """\
{:version 1
 :rules [{:name "boot"
          :on :boot
          :actions []}]}
"""

#: Phase F mixed member for the wake smoke: cheap auto-optout + cognitive
#: respond + boot (a complete, valid total spec).
MIXED_MEMBER_EDN = """\
{:version 1
 :rules [{:name "auto-optout"
          :on :mail-delivered
          :when (content-matches? msg "退订")
          :actions [(emit "已为您处理退订" [])]}
         {:name "respond"
          :on :mail-delivered
          :when (and (addressed-to-me? msg) (not (content-matches? msg "退订")))
          :loop {:context {:sections [(persona) (protocol)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :save-interaction true
                 :reflect {:enabled false :interval-minutes 5}}}
         {:name "boot"
          :on :boot
          :actions []}]}
"""


class _StaticProvider(AgentInfoProvider):
    """Read-only config provider backed by a dict."""

    def __init__(self, configs: dict[str, AgentConfig]):
        self._configs = configs

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)

    def get_all_ids(self) -> set[str]:
        return set(self._configs)


@pytest.fixture
async def bus():
    b = EventBus()
    await b.initialize()
    yield b
    await b.shutdown()


async def _wait_for(predicate, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out waiting for condition")


def _write_behavior(p: TlonPaths, agent_id: str, text: str) -> None:
    behavior = p.agent_behavior_file(agent_id)
    behavior.parent.mkdir(parents=True, exist_ok=True)
    behavior.write_text(text, encoding="utf-8")


class TestRegistryStart:
    async def test_start_deterministic_member(self, bus, tmp_path, monkeypatch):
        """The unified assembler starts a script member with derived runtime."""
        p = TlonPaths(home=tmp_path)
        monkeypatch.setattr("tlon.agents.loader.paths", p)
        _write_behavior(p, "relay-1", RELAY_EDN)

        registry = AgentRegistry(
            event_bus=bus,
            agent_info_provider=_StaticProvider(
                {"relay-1": AgentConfig(id="relay-1", name="转发者")}
            ),
            paths=p,
        )
        await registry.initialize()

        started: list[Event] = []
        bus.subscribe(EventType.AGENT_STARTED, lambda e: started.append(e))

        await bus.publish(
            Event(
                type=EventType.AGENT_START_REQUEST,
                payload=StartRequest(agent_id="relay-1", reason="boot"),
                source="adam",
            )
        )

        await _wait_for(lambda: registry.get_agent("relay-1") is not None)
        member = registry.get_agent("relay-1")
        assert member is not None
        assert member.member_type == "script"  # derived, no loop rule
        assert member.get_state() == "active"

        # AGENT_STARTED carries the derived runtime + spec provenance.
        await _wait_for(lambda: len(started) == 1)
        assert started[0].payload["agent_id"] == "relay-1"
        assert started[0].payload["runtime"] == "script"
        assert started[0].payload["spec_hash"] == load_member_spec("relay-1").source_hash

        await registry.shutdown()

    async def test_unknown_agent_refused(self, bus, tmp_path, monkeypatch):
        """A start request for an unregistered id is a no-op."""
        p = TlonPaths(home=tmp_path)
        monkeypatch.setattr("tlon.agents.loader.paths", p)
        registry = AgentRegistry(
            event_bus=bus,
            agent_info_provider=_StaticProvider({}),
            paths=p,
        )
        await registry.initialize()

        await bus.publish(
            Event(
                type=EventType.AGENT_START_REQUEST,
                payload=StartRequest(agent_id="nobody", reason="boot"),
                source="adam",
            )
        )
        await asyncio.sleep(0.2)
        assert registry.get_agent("nobody") is None

        await registry.shutdown()


class TestSpecDerivedLifecycle:
    """Phase E: the boot set and the lazy set come from the spec's :on rules."""

    async def _make_registry(self, bus, tmp_path, monkeypatch, configs, behaviors):
        p = TlonPaths(home=tmp_path)
        monkeypatch.setattr("tlon.agents.loader.paths", p)
        for agent_id, text in behaviors.items():
            _write_behavior(p, agent_id, text)
        registry = AgentRegistry(
            event_bus=bus,
            agent_info_provider=_StaticProvider(configs),
            paths=p,
        )
        await registry.initialize()
        for agent_id in configs:
            registry.register(agent_id)
        return registry

    async def test_boot_candidates_and_is_lazy(self, bus, tmp_path, monkeypatch):
        configs = {
            "booty-1": AgentConfig(id="booty-1", name="常驻"),
            "sleepy-1": AgentConfig(id="sleepy-1", name="延迟"),
        }
        registry = await self._make_registry(
            bus,
            tmp_path,
            monkeypatch,
            configs,
            {"booty-1": BOOTY_EDN, "sleepy-1": RELAY_EDN},  # sleepy-1: no :on :boot
        )

        assert registry.boot_candidates() == ["booty-1"]
        assert registry.is_lazy("booty-1") is False
        assert registry.is_lazy("sleepy-1") is True

        await registry.shutdown()

    async def test_contractor_always_lazy(self, bus, tmp_path, monkeypatch):
        """role=="contractor" is lazy (drainable) even with a boot rule."""
        configs = {
            "c-relay-a1b2c3d4": AgentConfig(
                id="c-relay-a1b2c3d4", name="外包", role="contractor"
            )
        }
        registry = await self._make_registry(
            bus, tmp_path, monkeypatch, configs, {"c-relay-a1b2c3d4": BOOTY_EDN}
        )

        # The boot set and the lazy set are independent derivations: the
        # spec declares :on :boot, so it is a boot candidate; but it is a
        # contractor, so it is always lazy (disposable, idle-reaped) and
        # will be drained back to zero after its idle timeout.
        assert "c-relay-a1b2c3d4" in registry.boot_candidates()
        assert registry.is_lazy("c-relay-a1b2c3d4") is True

        await registry.shutdown()

    async def test_unreadable_spec_is_lazy(self, bus, tmp_path, monkeypatch):
        """A member whose spec cannot be loaded has no boot rule to declare."""
        configs = {"ghost-1": AgentConfig(id="ghost-1", name="无规约")}
        registry = await self._make_registry(bus, tmp_path, monkeypatch, configs, {})

        assert registry.boot_candidates() == []
        assert registry.is_lazy("ghost-1") is True

        await registry.shutdown()


class TestRegistryQueries:
    """The shared host-query implementation (Adam + script members)."""

    def test_non_string_input_reads_as_false(self, tmp_path):
        """A missing payload key yields None: non-string input must read as
        False without ever reaching into the registry — the guard semantics
        the Adam/registry query merge () settles on."""
        registry = AgentRegistry(
            event_bus=EventBus(),
            agent_info_provider=_StaticProvider({}),
            paths=TlonPaths(home=tmp_path),
        )
        queries = RegistryQueries(registry)

        assert queries.is_sleeping(None) is False
        assert queries.is_running(None) is False
        assert queries.is_lazy(None) is False
        assert queries.is_lazy(42) is False
        assert queries.start_all_candidates() == []


class TestWakeOnMail:
    """Phase E: the registry derives wake-on-mail from each member's spec.

    Previously Adam's wake rule; the gate (registered, not running/waking,
    spec subscribes ``:on :mail-delivered``) is now a spec-derived
    mechanized policy in the registry.
    """

    async def _sleeping_registry(self, bus, tmp_path, monkeypatch, agent_id, text, running=False):
        p = TlonPaths(home=tmp_path)
        monkeypatch.setattr("tlon.agents.loader.paths", p)
        _write_behavior(p, agent_id, text)
        registry = AgentRegistry(
            event_bus=bus,
            agent_info_provider=_StaticProvider({agent_id: AgentConfig(id=agent_id, name=agent_id)}),
            paths=p,
        )
        await registry.initialize()
        if running:
            # Boot it first so the wake gate sees a live agent.
            await bus.publish(
                Event(
                    type=EventType.AGENT_START_REQUEST,
                    payload=StartRequest(agent_id=agent_id, reason="boot"),
                    source="adam",
                )
            )
            await _wait_for(lambda: registry.get_agent(agent_id) is not None)
        return registry

    async def _deliver(self, bus, recipient, trace: tuple | None = None):
        mail = Mail.create(
            from_address="alice@tlon.internal",
            content="wake up",
            to=[f"{recipient}@tlon.internal"],
        )
        kwargs = {"type": EventType.MAIL_DELIVERED,
                  "payload": {"mail": mail, "recipient": recipient},
                  "source": "mail_delivery"}
        if trace:
            kwargs["trace_id"], kwargs["span_id"], kwargs["parent_span_id"] = trace
        await bus.publish(Event(**kwargs))

    async def test_wake_sleeping_member(self, bus, tmp_path, monkeypatch):
        registry = await self._sleeping_registry(
            bus, tmp_path, monkeypatch, "worker-1", RELAY_EDN
        )
        starts: list[Event] = []
        bus.subscribe(EventType.AGENT_START_REQUEST, lambda e: starts.append(e))

        await self._deliver(bus, "worker-1")
        await _wait_for(lambda: len(starts) == 1)
        assert starts[0].payload.agent_id == "worker-1"
        assert starts[0].payload.reason == "wake"

        # The wake actually assembles the member.
        await _wait_for(lambda: registry.get_agent("worker-1") is not None)
        await registry.shutdown()

    async def test_wake_skips_running_member(self, bus, tmp_path, monkeypatch):
        registry = await self._sleeping_registry(
            bus, tmp_path, monkeypatch, "worker-1", RELAY_EDN, running=True
        )
        starts: list[Event] = []
        bus.subscribe(EventType.AGENT_START_REQUEST, lambda e: starts.append(e))

        await self._deliver(bus, "worker-1")
        await asyncio.sleep(0.2)
        assert len(starts) == 0  # already running — no wake request

        await registry.shutdown()

    async def test_wake_skips_unsubscribed_spec(self, bus, tmp_path, monkeypatch):
        """A spec without ``:on :mail-delivered`` is never woken by mail."""
        registry = await self._sleeping_registry(
            bus, tmp_path, monkeypatch, "quiet-1", BOOT_ONLY_EDN
        )
        starts: list[Event] = []
        bus.subscribe(EventType.AGENT_START_REQUEST, lambda e: starts.append(e))

        await self._deliver(bus, "quiet-1")
        await asyncio.sleep(0.2)
        assert len(starts) == 0

        await registry.shutdown()

    async def test_wake_joins_trace(self, bus, tmp_path, monkeypatch):
        """The wake request carries the delivery event's trace triple."""
        registry = await self._sleeping_registry(
            bus, tmp_path, monkeypatch, "worker-1", RELAY_EDN
        )
        starts: list[Event] = []
        bus.subscribe(EventType.AGENT_START_REQUEST, lambda e: starts.append(e))

        await self._deliver(bus, "worker-1", trace=("abc", "def", "ghi"))
        await _wait_for(lambda: len(starts) == 1)
        assert starts[0].trace_id == "abc"
        assert starts[0].span_id == "def"
        assert starts[0].parent_span_id == "ghi"

        await _wait_for(lambda: registry.get_agent("worker-1") is not None)
        await registry.shutdown()

    async def test_wake_mixed_member_scan_fires_deterministic_rule(
        self, bus, tmp_path, monkeypatch
    ):
        """Smoke (phase F): a 退订 mail that arrived while a mixed member
        slept is answered by the deterministic auto-optout on wake — the
        woken member's unread scan runs the cheap path even though a
        cognitive rule also matches the event."""
        p = TlonPaths(home=tmp_path)
        monkeypatch.setattr("tlon.agents.loader.paths", p)
        _write_behavior(p, "gu-yanxi-9", MIXED_MEMBER_EDN)
        registry = AgentRegistry(
            event_bus=bus,
            agent_info_provider=_StaticProvider(
                {"gu-yanxi-9": AgentConfig(id="gu-yanxi-9", name="客服")}
            ),
            paths=p,
        )
        await registry.initialize()

        # The mail sat in the sleeping member's inbox while she was down.
        mailbox = Mailbox(p.member_mail_dir("gu-yanxi-9"))
        optout = Mail.create(
            from_address="user@tlon.internal",
            content="我要退订",
            to=["gu-yanxi-9@tlon.internal"],
        )
        await mailbox.append_inbox(optout)

        replies: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, lambda e: replies.append(e))

        await self._deliver(bus, "gu-yanxi-9")
        await _wait_for(lambda: registry.get_agent("gu-yanxi-9") is not None)
        # Wake → unread scan → auto-optout replies to the sender.
        await _wait_for(lambda: len(replies) == 1)
        assert replies[0].payload.content == "已为您处理退订"
        assert replies[0].payload.to == ["user@tlon.internal"]

        await registry.shutdown()


class _FakeDockerRuntime:
    """Stand-in for DockerRuntime: records remove() calls, can fail."""

    def __init__(self, fail: bool = False):
        self.remove_calls: list[bool] = []
        self._fail = fail

    async def remove(self, force: bool = False) -> None:
        self.remove_calls.append(force)
        if self._fail:
            raise RuntimeError("daemon gone")


class _DisposableProvider(_StaticProvider):
    """Static provider that also deletes the agent home (rmtree)."""

    def __init__(self, configs: dict[str, AgentConfig], paths: TlonPaths):
        super().__init__(configs)
        self._paths = paths

    def delete_agent(self, agent_id: str) -> None:
        # tlon_home()/id instead of agent_dir(): the latter mkdirs on call.
        home = self._paths.tlon_home() / agent_id
        if not home.exists():
            raise ValueError(f"Agent '{agent_id}' not found")
        shutil.rmtree(home)
        self._configs.pop(agent_id, None)


class TestContainerReclaim:
    """: discarding a contractor reclaims its sandbox container.

    Both discard triggers (``stop_contractor`` and the idle drain's
    contractor branch) converge on ``_discard_contractor``; a resident's
    drain (AGENT_SLEEPING) must never touch the container.
    """

    async def _make_registry(
        self, bus, tmp_path, monkeypatch, configs: dict[str, AgentConfig], homes=()
    ):
        p = TlonPaths(home=tmp_path)
        monkeypatch.setattr("tlon.agents.loader.paths", p)
        for agent_id in homes:
            (p.tlon_home() / agent_id).mkdir(parents=True, exist_ok=True)
        registry = AgentRegistry(
            event_bus=bus,
            agent_info_provider=_DisposableProvider(configs, p),
            paths=p,
        )
        await registry.initialize()
        for agent_id in configs:
            registry.register(agent_id)
        return registry, p

    async def _start(self, bus, registry, agent_id: str) -> None:
        await bus.publish(
            Event(
                type=EventType.AGENT_START_REQUEST,
                payload=StartRequest(agent_id=agent_id, reason="boot"),
                source="adam",
            )
        )
        await _wait_for(lambda: registry.get_agent(agent_id) is not None)

    async def test_stop_contractor_reclaims_container(self, bus, tmp_path, monkeypatch):
        agent_id = "c-relay-a1b2c3d4"
        configs = {
            agent_id: AgentConfig(id=agent_id, name="外包", role="contractor")
        }
        registry, p = await self._make_registry(
            bus, tmp_path, monkeypatch, configs, homes=(agent_id,)
        )
        runtime = _FakeDockerRuntime()
        registry._docker_runtimes[agent_id] = runtime

        stopped: list[Event] = []
        bus.subscribe(EventType.AGENT_STOPPED, lambda e: stopped.append(e))

        assert await registry.stop_contractor(agent_id, stopped_by="test") is True

        # Container force-removed before the home dir went away.
        assert runtime.remove_calls == [True]
        assert agent_id not in registry._docker_runtimes
        assert not (p.tlon_home() / agent_id).exists()
        await _wait_for(lambda: len(stopped) == 1)
        assert stopped[0].payload["agent_id"] == agent_id

        await registry.shutdown()

    async def test_stop_contractor_without_runtime_skips_reclaim(
        self, bus, tmp_path, monkeypatch
    ):
        """daemon was unavailable at assembly: no handle, silent skip."""
        agent_id = "c-relay-a1b2c3d4"
        configs = {
            agent_id: AgentConfig(id=agent_id, name="外包", role="contractor")
        }
        registry, p = await self._make_registry(
            bus, tmp_path, monkeypatch, configs, homes=(agent_id,)
        )
        assert agent_id not in registry._docker_runtimes

        stopped: list[Event] = []
        bus.subscribe(EventType.AGENT_STOPPED, lambda e: stopped.append(e))

        assert await registry.stop_contractor(agent_id, stopped_by="test") is True
        assert not (p.tlon_home() / agent_id).exists()
        await _wait_for(lambda: len(stopped) == 1)

        await registry.shutdown()

    async def test_stop_contractor_reclaim_failure_still_discards(
        self, bus, tmp_path, monkeypatch
    ):
        """A remove() error only logs — home is deleted, event published."""
        agent_id = "c-relay-a1b2c3d4"
        configs = {
            agent_id: AgentConfig(id=agent_id, name="外包", role="contractor")
        }
        registry, p = await self._make_registry(
            bus, tmp_path, monkeypatch, configs, homes=(agent_id,)
        )
        runtime = _FakeDockerRuntime(fail=True)
        registry._docker_runtimes[agent_id] = runtime

        stopped: list[Event] = []
        bus.subscribe(EventType.AGENT_STOPPED, lambda e: stopped.append(e))

        assert await registry.stop_contractor(agent_id, stopped_by="test") is True
        assert runtime.remove_calls == [True]
        assert agent_id not in registry._docker_runtimes
        assert not (p.tlon_home() / agent_id).exists()
        await _wait_for(lambda: len(stopped) == 1)

        await registry.shutdown()

    async def test_idle_drain_contractor_reclaims_container(
        self, bus, tmp_path, monkeypatch
    ):
        """The idle drain's contractor branch converges on the same discard."""
        agent_id = "c-relay-a1b2c3d4"
        configs = {
            agent_id: AgentConfig(
                id=agent_id,
                name="外包",
                role="contractor",
                idle_timeout_seconds=0,
            )
        }
        registry, p = await self._make_registry(
            bus, tmp_path, monkeypatch, configs, homes=(agent_id,)
        )
        _write_behavior(p, agent_id, RELAY_EDN)
        await self._start(bus, registry, agent_id)
        runtime = _FakeDockerRuntime()
        registry._docker_runtimes[agent_id] = runtime

        stopped: list[Event] = []
        bus.subscribe(EventType.AGENT_STOPPED, lambda e: stopped.append(e))

        await registry._drain_agent(agent_id, reason="drain")

        assert runtime.remove_calls == [True]
        assert registry.get_agent(agent_id) is None
        assert not (p.tlon_home() / agent_id).exists()
        await _wait_for(lambda: len(stopped) == 1)
        assert stopped[0].payload["stopped_by"] == "idle_drain"

        await registry.shutdown()

    async def test_resident_drain_keeps_container(self, bus, tmp_path, monkeypatch):
        """Regression lock: a resident sleep never touches the container."""
        agent_id = "worker-1"
        configs = {
            agent_id: AgentConfig(id=agent_id, name="居民", idle_timeout_seconds=0)
        }
        registry, p = await self._make_registry(
            bus, tmp_path, monkeypatch, configs, homes=(agent_id,)
        )
        _write_behavior(p, agent_id, RELAY_EDN)  # no :on :boot → lazy resident
        await self._start(bus, registry, agent_id)
        runtime = _FakeDockerRuntime()
        registry._docker_runtimes[agent_id] = runtime

        sleeping: list[Event] = []
        bus.subscribe(EventType.AGENT_SLEEPING, lambda e: sleeping.append(e))

        await registry._drain_agent(agent_id, reason="drain")

        assert runtime.remove_calls == []
        assert registry._docker_runtimes[agent_id] is runtime
        assert registry.get_agent(agent_id) is None
        assert (p.tlon_home() / agent_id).exists()  # home survives
        await _wait_for(lambda: len(sleeping) == 1)

        await registry.shutdown()
