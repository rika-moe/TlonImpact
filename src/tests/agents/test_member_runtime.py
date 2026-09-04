"""MemberRuntime — the unified interpreter (phase C).

Pins the deterministic dispatch (all five action kinds, boot rules, the
unread rescue scan, the own-mail guard), the D6.1 mark-read semantics
(processed mail read immediately; a cognitive enqueue defers the read to
session completion; a gate-rejected mail is read anyway), the cognitive
routing (loop rules enqueue instead of executing inline), and the derived
member_type.

The cognitive tests exercise the routing with lightweight stand-ins for the
unchanged cognitive engine: ``_assemble_cognitive`` only stores its deps at
construction, so the routing logic under test never dereferences them.
"""

import asyncio
import inspect
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tlon.agents.edn import loads
from tlon.agents.eval import ActionRequest, FuelExhaustedError, make_member_builtins
from tlon.agents.member_runtime import CognitiveCapabilities, MemberRuntime
from tlon.agents.member_spec import MemberSpec, validate_member_spec
from tlon.agents.on_complete import OnCompleteRunner
from tlon.agents.session import AgentSession
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.trigger import AgentIdentity
from tlon.base.config import AgentConfig, LLMConfig
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.tools.base import Tool

# ── Specs ──────────────────────────────────────────────────────────────

SCRIPT_FORWARD = """\
{:version 1
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["sink@tlon.internal"]})]}]}
"""

SCRIPT_START_STOP = """\
{:version 1
 :rules [{:name "boot"
          :on :boot
          :actions [(start {:agent "worker-1" :reason "boot"})]}
         {:name "idle-stop"
          :on :agent-idle
          :actions [(stop {:agent "worker-1" :reason "drain"})]}]}
"""

SCRIPT_EMIT = """\
{:version 1
 :rules [{:name "notify"
          :on :agent-started
          :actions [(emit "hello world" ["sink@tlon.internal"])]}]}
"""

SCRIPT_GATED = """\
{:version 1
 :rules [{:name "urgent"
          :on :mail-delivered
          :when (content-matches? msg "URGENT")
          :actions [(forward {:to ["sink@tlon.internal"]})]}]}
"""

SCRIPT_SPAWN = """\
{:version 1
 :rules [{:name "hire"
          :on :mail-delivered
          :actions [(spawn "developer")]}]}
"""

COGNITIVE_EDN = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :when (or (addressed-to-me? msg) (cc-ed? msg))
          :loop {:context {:sections [(persona) (protocol)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :reflect {:enabled false :interval-minutes 5}}}]}
"""

FUELED_LOOP_GATE = """\
{:version 1
 :rules [{:name "loopy"
          :on :mail-delivered
          :when (while [i 0] (< i 100) (recur (inc i)))
          :fuel 40
          :actions [(emit "should never fire" ["sink@tlon.internal"])]}]}
"""

LOOP_EMIT = """\
{:version 1
 :rules [{:name "fanout"
          :on :mail-delivered
          :when (addressed-to-me? msg)
          :actions [(map i (emit "x" ["sink@tlon.internal"]) [1 2 3])]}]}
"""

COGNITIVE_LOOP_ACTIONS = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :when (addressed-to-me? msg)
          :loop {:context {:sections [(persona) (protocol)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :reflect {:enabled false :interval-minutes 5}}
          :actions [(while [i 0] (< i 3) (emit "done" ["sink@tlon.internal"]) (recur (inc i)))]}]}
"""

COGNITIVE_EXHAUSTED = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :when (addressed-to-me? msg)
          :fuel 30
          :loop {:context {:sections [(persona) (protocol)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :reflect {:enabled false :interval-minutes 5}}
          :actions [(while [i 0] (< i 1000) (emit "never" ["sink@tlon.internal"]) (recur (inc i)))]}]}
"""

COGNITIVE_WITH_ACTIONS = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :when (addressed-to-me? msg)
          :loop {:context {:sections [(persona) (protocol)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :reflect {:enabled false :interval-minutes 5}}
          :actions [(emit "done" ["sink@tlon.internal"])]}]}
"""

#: Phase F mixed member (the deflection/cost-routing shape): a cheap
#: deterministic auto-optout rule in front of the cognitive respond rule,
#: whose :when excludes whatever the cheap path handles.
MIXED_EDN = """\
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
                 :reflect {:enabled false :interval-minutes 5}}}]}
"""


# ── Cognitive engine stand-ins (the routing path never dereferences them) ──


class _StubLLM:
    """Duck-typed LLMClient: never invoked without a running scheduler."""

    async def chat(self, **kwargs: Any) -> Any:
        raise AssertionError("no session should run without the scheduler")


class _MemoryStub:
    """Duck-typed MemoryManager.

    ``config`` is read by ReflectionEngine at assembly time; the routing
    path never calls the memory methods.
    """

    config: Any = None

    async def load(self) -> None: ...


class _TaskStub:
    """Duck-typed TaskManager."""

    def load(self) -> None: ...


# ── Fixtures / helpers ─────────────────────────────────────────────────


@pytest.fixture
async def bus():
    b = EventBus()
    await b.initialize()
    yield b
    await b.shutdown()


@pytest.fixture
def mail_dir(tmp_path: Path) -> Path:
    d = tmp_path / "mail"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_member(
    member_id: str,
    edn: str,
    event_bus: EventBus,
    *,
    mailbox: Mailbox | None = None,
    cognitive: CognitiveCapabilities | None = None,
    registry: Any = None,
) -> MemberRuntime:
    spec = MemberSpec.from_edn(loads(edn))
    validate_member_spec(spec)
    return MemberRuntime(
        member_id=member_id,
        name=member_id,
        event_bus=event_bus,
        spec=spec,
        builtins=make_member_builtins(),
        mailbox=mailbox,
        cognitive=cognitive,
        registry=registry,
    )


async def _capture(events: list[Event], event: Event) -> None:
    events.append(event)


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    """Poll until ``predicate`` is truthy; await it if it's a coroutine."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("timed out waiting for condition")


def _deliver(mail: Mail, member_id: str) -> Event:
    return Event(
        type=EventType.MAIL_DELIVERED,
        payload={"mail": mail, "recipient": member_id},
        source="mail_delivery",
    )


# ── Deterministic dispatch ─────────────────────────────────────────────


class TestDeterministicDispatch:
    async def test_forward_on_delivery_and_mark_read(self, bus, mail_dir):
        mailbox = Mailbox(mail_dir)
        member = _make_member("relay-1", SCRIPT_FORWARD, bus, mailbox=mailbox)
        await member.initialize()

        delivered: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, delivered))

        original = Mail.create(
            from_address="alice@tlon.internal", content="hello", to=[member.address]
        )
        await mailbox.append_inbox(original)
        await bus.publish(_deliver(original, member.member_id))

        await _wait_for(lambda: len(delivered) == 1)
        assert delivered[0].payload.forward_of == original.id
        assert delivered[0].payload.content == original.content
        # D6.1: a deterministic hit reads the mail immediately.
        assert await mailbox.unread() == []

        await member.shutdown()

    async def test_start_stop_requests(self, bus):
        member = _make_member("admin-1", SCRIPT_START_STOP, bus)
        starts: list[Event] = []
        stops: list[Event] = []
        bus.subscribe(EventType.AGENT_START_REQUEST, partial(_capture, starts))
        bus.subscribe(EventType.AGENT_STOP_REQUEST, partial(_capture, stops))

        await member.initialize()  # the :boot rule fires here
        await _wait_for(lambda: len(starts) == 1)
        assert starts[0].payload.agent_id == "worker-1"
        assert starts[0].payload.reason == "boot"

        await bus.publish(
            Event(type=EventType.AGENT_IDLE, payload={"agent_id": "worker-1"}, source="registry")
        )
        await _wait_for(lambda: len(stops) == 1)
        assert stops[0].payload.agent_id == "worker-1"
        assert stops[0].payload.reason == "drain"

        await member.shutdown()

    async def test_emit_sends_mail(self, bus):
        member = _make_member("notifier-1", SCRIPT_EMIT, bus)
        await member.initialize()
        emitted: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, emitted))

        await bus.publish(
            Event(
                type=EventType.AGENT_STARTED, payload={"agent_id": "someone"}, source="registry"
            )
        )
        await _wait_for(lambda: len(emitted) == 1)
        assert emitted[0].payload.content == "hello world"
        assert "sink@tlon.internal" in emitted[0].payload.to

        await member.shutdown()

    async def test_spawn_delegates_to_registry(self, bus):
        class _StubRegistry:
            def __init__(self) -> None:
                self.calls: list[tuple] = []

            async def spawn_member(self, *, prototype, name, spawner_id, session_id):
                self.calls.append((prototype, name, spawner_id, session_id))

        registry = _StubRegistry()
        member = _make_member("admin-2", SCRIPT_SPAWN, bus, registry=registry)
        await member.initialize()

        mail = Mail.create(
            from_address="alice@tlon.internal", content="hire someone", to=[member.address]
        )
        await bus.publish(_deliver(mail, member.member_id))
        await _wait_for(lambda: len(registry.calls) == 1)
        assert registry.calls[0][0] == "developer"
        assert registry.calls[0][2] == "admin-2"  # spawner_id
        assert registry.calls[0][3] is None  # no session context

        await member.shutdown()


class TestPhaseDFuel:
    """Phase D: rule-level :fuel is the rule's total evaluation budget."""

    def test_combined_fuel_gate_exhaustion(self, bus):
        """With :fuel set, the gate and actions share one counter — a
        runaway gate eats the whole allowance before any action evaluates.
        """
        member = _make_member("fueled-1", FUELED_LOOP_GATE, bus)
        rule = member._spec.rules[0]
        env = member._env({"mail": None, "recipient": member.member_id})
        with pytest.raises(FuelExhaustedError):
            member._evaluate_rule(rule, env)

    async def test_gate_fuel_exhaustion_safely_skips(self, bus, mail_dir):
        """Fuel exhaustion at the gate is logged distinctly and skips the
        rule — no action fires, and the mail is still marked read (D6.1).
        """
        mailbox = Mailbox(mail_dir)
        member = _make_member("fueled-1", FUELED_LOOP_GATE, bus, mailbox=mailbox)
        await member.initialize()
        emitted: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, emitted))

        mail = Mail.create(
            from_address="alice@tlon.internal", content="hello", to=[member.address]
        )
        with patch("tlon.agents.member_runtime.logger") as mock_logger:
            await member._on_event(_deliver(mail, member.member_id))
        warning_events = [c.args[0] for c in mock_logger.warning.call_args_list]
        assert "member_rule_fuel_exhausted" in warning_events
        assert emitted == []
        assert await mailbox.unread() == []

        await member.shutdown()

    async def test_loop_action_collects_multiple_requests(self, bus):
        """A loop-valued action (map of emits) yields several requests — the
        engine executes each one (dsl-agents.md: 'loop body actions are just
        more collected requests').
        """
        member = _make_member("fanout-1", LOOP_EMIT, bus)
        await member.initialize()
        emitted: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, emitted))

        mail = Mail.create(
            from_address="alice@tlon.internal", content="hi", to=[member.address]
        )
        await bus.publish(_deliver(mail, member.member_id))
        await _wait_for(lambda: len(emitted) == 3)
        assert all(event.payload.content == "x" for event in emitted)

        await member.shutdown()


class TestOnCompleteLoopActions:
    """Phase D: an on-complete (while ... (emit ...)) action records every
    body emit into the collector and the engine executes each one."""

    async def test_loop_action_collects_and_executes(self):
        spec = MemberSpec.from_edn(loads(COGNITIVE_LOOP_ACTIONS))
        validate_member_spec(spec)
        rule = spec.rules[0]

        executed: list[ActionRequest] = []

        async def _execute(request: ActionRequest, mail: Mail | None) -> None:
            executed.append(request)

        runner = OnCompleteRunner(
            identity=AgentIdentity(
                id="thinker-1", name="thinker-1", address="thinker-1@tlon.internal"
            ),
            rule=rule,
            logger=get_logger("test"),
            execute_action=_execute,
        )
        session = AgentSession(session_id="s-1", trigger_content="done")
        await runner.run(session, "done")
        assert len(executed) == 3
        assert all(request.kind == "emit" for request in executed)
        assert all(request.data["content"] == "done" for request in executed)

    async def test_loop_action_fuel_cap_limits_loop(self):
        """The rule's :fuel caps the completion actions: a runaway loop is
        cut short and logged, never raised, and requests collected before
        the cut still execute."""
        spec = MemberSpec.from_edn(loads(COGNITIVE_EXHAUSTED))
        validate_member_spec(spec)
        rule = spec.rules[0]

        executed: list[ActionRequest] = []

        async def _execute(request: ActionRequest, mail: Mail | None) -> None:
            executed.append(request)

        runner = OnCompleteRunner(
            identity=AgentIdentity(
                id="thinker-1", name="thinker-1", address="thinker-1@tlon.internal"
            ),
            rule=rule,
            logger=MagicMock(),
            execute_action=_execute,
        )
        session = AgentSession(session_id="s-1", trigger_content="done")
        await runner.run(session, "done")  # never raises
        assert 0 < len(executed) < 1000  # the 1000-iteration loop was fuel-capped
        assert all(request.kind == "emit" for request in executed)
        assert "on_complete_fuel_exhausted" in [
            c.args[0] for c in runner._logger.error.call_args_list
        ]


class TestMarkReadSemantics:
    async def test_gate_rejected_mail_is_marked_read(self, bus, mail_dir):
        """D6.1: a mail the :when gate rejects is read anyway."""
        mailbox = Mailbox(mail_dir)
        member = _make_member("gate-1", SCRIPT_GATED, bus, mailbox=mailbox)
        await member.initialize()

        delivered: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, delivered))

        plain = Mail.create(
            from_address="alice@tlon.internal", content="just a note", to=[member.address]
        )
        await mailbox.append_inbox(plain)
        await bus.publish(_deliver(plain, member.member_id))

        async def _no_unread() -> bool:
            return not await mailbox.unread()

        await _wait_for(_no_unread)  # processed → read
        assert delivered == []  # gate failed: no forward
        assert await mailbox.unread() == []

        await member.shutdown()

    async def test_gate_pass_marks_read(self, bus, mail_dir):
        mailbox = Mailbox(mail_dir)
        member = _make_member("gate-2", SCRIPT_GATED, bus, mailbox=mailbox)
        await member.initialize()
        delivered: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, delivered))

        urgent = Mail.create(
            from_address="alice@tlon.internal", content="URGENT please act", to=[member.address]
        )
        await mailbox.append_inbox(urgent)
        await bus.publish(_deliver(urgent, member.member_id))
        await _wait_for(lambda: len(delivered) == 1)
        assert await mailbox.unread() == []

        await member.shutdown()

    async def test_own_mail_is_ignored(self, bus, mail_dir):
        mailbox = Mailbox(mail_dir)
        member = _make_member("relay-1", SCRIPT_FORWARD, bus, mailbox=mailbox)
        await member.initialize()
        delivered: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, delivered))

        own = Mail.create(from_address=member.address, content="echo", to=["sink@tlon.internal"])
        await mailbox.append_inbox(own)
        await bus.publish(_deliver(own, member.member_id))

        async def _no_unread() -> bool:
            return not await mailbox.unread()

        await _wait_for(_no_unread)
        assert delivered == []  # own mail never processed
        # …but marked read immediately (an all@ self-copy must not linger)
        assert await mailbox.unread() == []

        await member.shutdown()


class TestBootAndUnread:
    async def test_boot_rule_runs_on_initialize(self, bus):
        member = _make_member("admin-1", SCRIPT_START_STOP, bus)
        starts: list[Event] = []
        bus.subscribe(EventType.AGENT_START_REQUEST, partial(_capture, starts))
        await member.initialize()
        await _wait_for(lambda: len(starts) == 1)
        assert starts[0].payload.reason == "boot"
        await member.shutdown()

    async def test_unread_scan_replays_mail_on_boot(self, bus, mail_dir):
        """Mail that arrived while the member slept is replayed at boot."""
        mailbox = Mailbox(mail_dir)
        sleeper = Mail.create(
            from_address="alice@tlon.internal",
            content="while asleep",
            to=["sink@tlon.internal"],
        )
        await mailbox.append_inbox(sleeper)

        member = _make_member("relay-1", SCRIPT_FORWARD, bus, mailbox=mailbox)
        delivered: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, delivered))

        await member.initialize()  # runs the unread scan
        await _wait_for(lambda: len(delivered) == 1)
        assert delivered[0].payload.forward_of == sleeper.id
        assert await mailbox.unread() == []

        await member.shutdown()


class TestDerivedMemberType:
    def test_deterministic_member_is_script(self, bus):
        member = _make_member("relay-1", SCRIPT_FORWARD, bus)
        assert member.member_type == "script"

    def test_loop_rule_member_is_react(self, bus):
        """A loop-rule spec derives react even without cognitive capabilities."""
        member = _make_member("thinker-1", COGNITIVE_EDN, bus)
        assert member.member_type == "react"


# ── Cognitive routing (D6.1 / loop-first-then-actions) ─────────────────


def _cognitive_caps(config: AgentConfig, tlon_paths: TlonPaths) -> CognitiveCapabilities:
    return CognitiveCapabilities(
        config=config,
        llm_client=_StubLLM(),
        memory=_MemoryStub(),
        task_manager=_TaskStub(),
        tool_executor=ToolExecutor(),
        team_members=[],
        tlon_paths=tlon_paths,
    )


def _make_cognitive_member(
    member_id: str, edn: str, event_bus: EventBus, mailbox: Mailbox
) -> MemberRuntime:
    config = AgentConfig(id=member_id, name=member_id, llm=LLMConfig(model_name="test"))
    return _make_member(
        member_id,
        edn,
        event_bus,
        mailbox=mailbox,
        cognitive=_cognitive_caps(config, TlonPaths(home=mailbox._dir.parent)),
    )


class TestCognitiveRouting:
    async def test_matching_mail_enqueued_and_read_deferred(self, bus, mail_dir):
        mailbox = Mailbox(mail_dir)
        member = _make_cognitive_member("thinker-1", COGNITIVE_EDN, bus, mailbox)
        # Constructed (scheduler assembled, not started); routing happens via
        # _on_event — the enqueue defers the read to session completion.
        incoming = Mail.create(
            from_address="alice@tlon.internal", content="think about this", to=[member.address]
        )
        await mailbox.append_inbox(incoming)
        await member._on_event(_deliver(incoming, member.member_id))
        # D6.1: enqueued for a session → not marked read yet.
        assert [m.id for m in await mailbox.unread()] == [incoming.id]

    async def test_gate_rejected_mail_marked_read(self, bus, mail_dir):
        mailbox = Mailbox(mail_dir)
        member = _make_cognitive_member("thinker-2", COGNITIVE_EDN, bus, mailbox)
        unrelated = Mail.create(
            from_address="alice@tlon.internal", content="not for you", to=["other@tlon.internal"]
        )
        await mailbox.append_inbox(unrelated)
        await member._on_event(_deliver(unrelated, member.member_id))
        assert await mailbox.unread() == []

    async def test_cognitive_actions_not_run_inline(self, bus, mail_dir):
        """A hit runs the loop first, then actions — never inline (D6.2)."""
        mailbox = Mailbox(mail_dir)
        member = _make_cognitive_member("thinker-3", COGNITIVE_WITH_ACTIONS, bus, mailbox)
        emitted: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, emitted))

        incoming = Mail.create(
            from_address="alice@tlon.internal", content="do it", to=[member.address]
        )
        await mailbox.append_inbox(incoming)
        await member._on_event(_deliver(incoming, member.member_id))
        await asyncio.sleep(0.1)
        assert emitted == []  # the :actions (emit) wait for the session to complete


class _RecordingTool(Tool):
    """Duck-simple tool recording whether shutdown() was invoked."""

    def __init__(self) -> None:
        super().__init__(name="recorder", description="", parameters={"type": "object"})
        self.shutdown_called = False

    async def execute(self, **kwargs: Any) -> Any:
        raise AssertionError("never executed")

    async def shutdown(self) -> None:
        self.shutdown_called = True


class TestToolShutdownWiring:
    async def test_member_shutdown_shuts_down_tool_executor(self, bus, mail_dir):
        """MemberRuntime.shutdown must cascade into tool_executor.shutdown so
        tools (e.g. ExecuteBashTool) can cancel their background tasks."""
        mailbox = Mailbox(mail_dir)
        config = AgentConfig(id="thinker-x", name="thinker-x", llm=LLMConfig(model_name="test"))
        tool_executor = ToolExecutor()
        recorder = _RecordingTool()
        tool_executor.register_tool(recorder)
        caps = CognitiveCapabilities(
            config=config,
            llm_client=_StubLLM(),
            memory=_MemoryStub(),
            task_manager=_TaskStub(),
            tool_executor=tool_executor,
            team_members=[],
            tlon_paths=TlonPaths(home=mail_dir.parent),
        )
        member = _make_member("thinker-x", COGNITIVE_EDN, bus, mailbox=mailbox, cognitive=caps)

        await member.shutdown()

        assert recorder.shutdown_called


# ── Phase F: mixed member (deflection / cost routing) ──────────────────


class TestMixedMemberDeflection:
    """A deterministic cheap path in front of a cognitive rule.

    Cost routing: a 退订 mail hits only the deterministic auto-optout rule
    (reply, zero LLM); everything else addressed to the member enqueues a
    cognitive session. The boot unread scan must run the deterministic
    rule even though a cognitive rule also matches the event (regression
    for the mixed-member gap).
    """

    async def test_unsubscribe_deflected_to_deterministic(self, bus, mail_dir):
        mailbox = Mailbox(mail_dir)
        member = _make_cognitive_member("gu-yanxi-1", MIXED_EDN, bus, mailbox)
        emitted: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, emitted))

        optout = Mail.create(
            from_address="user@tlon.internal", content="我要退订", to=[member.address]
        )
        await mailbox.append_inbox(optout)
        await member._on_event(_deliver(optout, member.member_id))

        # The deterministic auto-optout replied to the sender (empty
        # recipients → reply-to-sender on the deterministic path, phase F).
        await _wait_for(lambda: len(emitted) == 1)
        assert emitted[0].payload.content == "已为您处理退订"
        assert emitted[0].payload.to == ["user@tlon.internal"]
        # No cognitive session: the mail is marked read immediately.
        assert await mailbox.unread() == []

    async def test_normal_mail_enqueues_cognitive(self, bus, mail_dir):
        mailbox = Mailbox(mail_dir)
        member = _make_cognitive_member("gu-yanxi-2", MIXED_EDN, bus, mailbox)
        emitted: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, emitted))

        normal = Mail.create(
            from_address="user@tlon.internal", content="咨询一下", to=[member.address]
        )
        await mailbox.append_inbox(normal)
        await member._on_event(_deliver(normal, member.member_id))

        await asyncio.sleep(0.1)
        assert emitted == []  # no deterministic reply
        # D6.1: enqueued for a session → read deferred.
        assert [m.id for m in await mailbox.unread()] == [normal.id]

    async def test_unread_scan_runs_deterministic_rule_for_mixed_member(self, bus, mail_dir):
        """Regression (phase F): the boot scan must not swallow the cheap
        path for a mixed member — a 退订 mail that arrived while sleeping
        gets the deterministic auto-optout reply on wake."""
        mailbox = Mailbox(mail_dir)
        optout = Mail.create(
            from_address="user@tlon.internal", content="退订谢谢", to=["gu-yanxi-3@tlon.internal"]
        )
        await mailbox.append_inbox(optout)

        member = _make_cognitive_member("gu-yanxi-3", MIXED_EDN, bus, mailbox)
        emitted: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, emitted))

        await member.initialize()  # runs the unread scan
        await _wait_for(lambda: len(emitted) == 1)
        assert emitted[0].payload.content == "已为您处理退订"
        assert emitted[0].payload.to == ["user@tlon.internal"]
        assert await mailbox.unread() == []

        await member.shutdown()


# ── Boot-scan throttling (issue ) ────────────────────────────────────


class TestBootScanThrottle:
    """The boot unread scan throttles a mail backlog.

    One thread merges into one session (the thread's latest unread mail is
    the representative; earlier siblings are marked read as covered — the
    session's mail-history context renders the whole thread), and overflow
    the bounded queue cannot take stays fully unread for the deferred
    rescan, which trickles it in as the scheduler drains. Nothing is
    dropped on the floor and no mail is enqueued twice.

    The scheduler itself is never started here (its sessions would need a
    real LLM); the tests drive the scan and the queue directly, the same
    way TestCognitiveRouting drives ``_on_event``.
    """

    @staticmethod
    def _make_throttled_member(
        member_id: str, bus: EventBus, mailbox: Mailbox, *, queue_size: int
    ) -> MemberRuntime:
        config = AgentConfig(
            id=member_id,
            name=member_id,
            llm=LLMConfig(model_name="test"),
            message_queue_size=queue_size,
        )
        return _make_member(
            member_id,
            COGNITIVE_EDN,
            bus,
            mailbox=mailbox,
            cognitive=_cognitive_caps(config, TlonPaths(home=mailbox._dir.parent)),
        )

    @staticmethod
    def _drain_queue(member: MemberRuntime) -> list[Mail]:
        """Dequeue every queued mail (simulating the scheduler's intake)."""
        scheduler = member._scheduler
        assert scheduler is not None
        drained: list[Mail] = []
        while True:
            try:
                mail, _carrier = scheduler._mail_queue.get_nowait()
            except asyncio.QueueEmpty:
                return drained
            drained.append(mail)

    async def test_same_thread_unread_merges_into_one_session(self, bus, mail_dir):
        mailbox = Mailbox(mail_dir)
        member = _make_cognitive_member("thinker-t1", COGNITIVE_EDN, bus, mailbox)
        root = Mail.create(
            from_address="alice@tlon.internal", content="first", to=[member.address]
        )
        reply = Mail.create(
            from_address="alice@tlon.internal",
            content="second",
            to=[member.address],
            in_reply_to=root.id,
            thread_id=root.thread_id,
        )
        other = Mail.create(
            from_address="alice@tlon.internal", content="separate", to=[member.address]
        )
        for mail in (root, reply, other):
            await mailbox.append_inbox(mail)

        await member._scan_unread_mail()

        # The thread's latest mail represents it; the separate thread is
        # its own session.
        assert [m.id for m in self._drain_queue(member)] == [reply.id, other.id]
        assert member._scan_pending == {reply.id, other.id}
        # The merged sibling is read as covered; the representatives defer
        # their read to session completion (D6.1).
        assert [m.id for m in await mailbox.unread()] == [reply.id, other.id]
        assert member._scan_deferred is False

    async def test_single_unread_enqueued_as_before(self, bus, mail_dir):
        """No backlog, no sharing: behavior is unchanged."""
        mailbox = Mailbox(mail_dir)
        member = _make_cognitive_member("thinker-t2", COGNITIVE_EDN, bus, mailbox)
        mail = Mail.create(
            from_address="alice@tlon.internal", content="hello", to=[member.address]
        )
        await mailbox.append_inbox(mail)

        await member._scan_unread_mail()

        assert [m.id for m in self._drain_queue(member)] == [mail.id]
        assert [m.id for m in await mailbox.unread()] == [mail.id]  # read deferred
        assert member._scan_deferred is False

    async def test_overflow_stays_unread_and_rescan_picks_up(self, bus, mail_dir):
        mailbox = Mailbox(mail_dir)
        member = self._make_throttled_member("thinker-t3", bus, mailbox, queue_size=2)
        mails = [
            Mail.create(
                from_address="alice@tlon.internal",
                content=f"backlog {i}",
                to=[member.address],
            )
            for i in range(4)
        ]
        for mail in mails:
            await mailbox.append_inbox(mail)

        await member._scan_unread_mail()
        assert [m.id for m in self._drain_queue(member)] == [mails[0].id, mails[1].id]
        assert member._scan_deferred is True
        # The overflow is fully unread — queued mail defers its read,
        # dropped mail keeps it (deterministic rules deferred with it).
        assert [m.id for m in await mailbox.unread()] == [m.id for m in mails]

        # Sessions complete (their read marks land) and the rescan
        # trickles the overflow in — nothing lost, nothing enqueued twice.
        await member._mark_read(mails[0])
        await member._mark_read(mails[1])
        await member._scan_unread_mail()
        assert [m.id for m in self._drain_queue(member)] == [mails[2].id, mails[3].id]
        assert member._scan_pending == {mails[2].id, mails[3].id}
        assert member._scan_deferred is False

    async def test_deferred_rescan_trickles_overflow_until_done(self, bus, mail_dir):
        mailbox = Mailbox(mail_dir)
        member = self._make_throttled_member("thinker-t4", bus, mailbox, queue_size=2)
        mails = [
            Mail.create(
                from_address="alice@tlon.internal",
                content=f"backlog {i}",
                to=[member.address],
            )
            for i in range(5)
        ]
        for mail in mails:
            await mailbox.append_inbox(mail)

        await member._scan_unread_mail()
        assert member._scan_deferred is True

        # The scheduler stays stopped; the test drains the queue and marks
        # read, standing in for session completions.
        member._initialized = True
        with patch("tlon.agents.member_runtime._RESCAN_POLL_SECONDS", 0.02):
            task = asyncio.create_task(member._rescan_when_drained())
            processed: list[str] = []
            while len(processed) < len(mails):
                for mail in self._drain_queue(member):
                    processed.append(mail.id)
                    await member._mark_read(mail)
                await asyncio.sleep(0.01)
            await asyncio.wait_for(task, timeout=2.0)

        # Every mail was picked up exactly once; the scan went clean and
        # the rescan task exited on its own.
        assert sorted(processed) == sorted(m.id for m in mails)
        assert member._scan_deferred is False
        assert await mailbox.unread() == []

        await member.shutdown()
