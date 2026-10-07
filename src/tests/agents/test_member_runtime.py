"""MemberRuntime — the unified interpreter (phase C).

Pins the deterministic dispatch (all five action kinds, boot rules, the
unread rescue scan, the own-mail guard), the D6.1 mark-read semantics
(processed mail read immediately; a cognitive enqueue defers the read to
session completion; a gate-rejected mail is read anyway), and the cognitive
routing (loop rules enqueue instead of executing inline).

The cognitive tests exercise the routing with lightweight stand-ins for the
unchanged cognitive engine: ``_assemble_cognitive`` only stores its deps at
construction, so the routing logic under test never dereferences them.
"""

import asyncio
import inspect
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tlon.agents.member_runtime import CognitiveCapabilities, MemberRuntime
from tlon.agents.member_spec import MemberSpec, validate_member_spec
from tlon.agents.on_complete import OnCompleteRunner
from tlon.agents.session import AgentSession
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.trigger import AgentIdentity
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.config import AgentConfig, LLMConfig
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMResponse
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.memory.models import MemoryConfig
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.tools.base import Tool
from tlosure.builtins import Builtin
from tlosure.edn import loads
from tlosure.eval import ActionRequest, FuelExhaustedError

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
                 :max-tokens 1000000
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

#: ADR 0021: a defn helper defined in one action form is called from a
#: later form; the emit inside the function body is collected (return
#: value on the deterministic path).
SCRIPT_DEFN = """\
{:version 1
 :rules [{:name "notify"
          :on :mail-delivered
          :when (addressed-to-me? msg)
          :actions [(defn notify [text] (emit (str "note: " text) ["sink@tlon.internal"]))
                    (notify "hello")]}]}
"""

SCRIPT_DEFN_FUELED = """\
{:version 1
 :rules [{:name "notify"
          :on :mail-delivered
          :fuel 500
          :actions [(defn notify [text] (emit (str "note: " text) ["sink@tlon.internal"]))
                    (notify "hello")]}]}
"""

COGNITIVE_DEFN_ACTIONS = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :when (addressed-to-me? msg)
          :loop {:context {:sections [(persona) (protocol)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :max-tokens 1000000
                 :reflect {:enabled false :interval-minutes 5}}
          :actions [(defn notify [text] (emit (str "done: " text) ["sink@tlon.internal"]))
                    (notify result)]}]}
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
                 :max-tokens 1000000
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
                 :max-tokens 1000000
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
                 :max-tokens 1000000
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
                 :max-tokens 1000000
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

    config: Any = MemoryConfig()

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
    builtins: dict[str, Builtin] | None = None,
) -> MemberRuntime:
    spec = MemberSpec.from_edn(loads(edn))
    registry_builtins = builtins if builtins is not None else make_member_builtins()
    validate_member_spec(spec, builtins=registry_builtins)
    return MemberRuntime(
        member_id=member_id,
        name=member_id,
        event_bus=event_bus,
        spec=spec,
        builtins=registry_builtins,
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
            Event(type=EventType.AGENT_STARTED, payload={"agent_id": "someone"}, source="registry")
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


class TestCapabilityWordDispatch:
    """The vocabulary bridge end to end (issue #45 slice 2/3): a trusted
    capability word composed in a deterministic rule's actions evaluates
    to its ActionRequest data and the engine executes it."""

    CAPABILITY = """\
{:version 1
 :name "notify-sink"
 :description "给 sink 发一封带前缀的通知邮件"
 :args [{:name "text" :type :string :doc "通知正文"}]
 :impl (fn [text] (emit (str "note: " text) ["sink@tlon.internal"]))
 :pure false}
"""

    SPEC = """\
{:version 1
 :rules [{:name "notify"
          :on :mail-delivered
          :when (addressed-to-me? msg)
          :actions [(notify-sink "hello")]}]}
"""

    async def test_capability_word_produces_action_request(self, bus, tmp_path):
        from tlon.agents.capabilities.bridge import load_capability_builtins

        paths = TlonPaths(home=tmp_path)
        cap_dir = paths.capabilities_dir() / "notify-sink"
        cap_dir.mkdir(parents=True)
        (cap_dir / "capability.tlon").write_text(self.CAPABILITY, encoding="utf-8")

        host = make_member_builtins()
        builtins = {**host, **load_capability_builtins(paths, reserved=host)}
        member = _make_member("notifier-2", self.SPEC, bus, builtins=builtins)
        await member.initialize()

        emitted: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, emitted))
        mail = Mail.create(from_address="alice@tlon.internal", content="ping", to=[member.address])
        await bus.publish(_deliver(mail, member.member_id))

        await _wait_for(lambda: len(emitted) == 1)
        assert emitted[0].payload.content == "note: hello"
        assert "sink@tlon.internal" in emitted[0].payload.to

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

        mail = Mail.create(from_address="alice@tlon.internal", content="hello", to=[member.address])
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

        mail = Mail.create(from_address="alice@tlon.internal", content="hi", to=[member.address])
        await bus.publish(_deliver(mail, member.member_id))
        await _wait_for(lambda: len(emitted) == 3)
        assert all(event.payload.content == "x" for event in emitted)

        await member.shutdown()


class TestDefnAcrossActionForms:
    """ADR 0021: defn names thread across a rule's action forms.

    Load-time validation (define-before-use) is pinned in
    ``test_member_spec.TestDefnThreading``; here the runtime side: the
    forms of one rule share one env dict, so a defn helper is callable
    from later forms, and an ``emit`` inside the function body converges
    — by return value on the deterministic path, by collector on the
    on-complete path.
    """

    async def test_defn_helper_sends_mail(self, bus):
        member = _make_member("notifier-2", SCRIPT_DEFN, bus)
        await member.initialize()
        emitted: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, emitted))

        mail = Mail.create(from_address="alice@tlon.internal", content="hi", to=[member.address])
        await bus.publish(_deliver(mail, member.member_id))
        await _wait_for(lambda: len(emitted) == 1)
        assert emitted[0].payload.content == "note: hello"
        assert emitted[0].payload.to == ["sink@tlon.internal"]

        await member.shutdown()

    def test_defn_helper_under_shared_fuel(self, bus):
        """The :fuel path shares the same env: the defn is visible to the
        later form, and its body's emit lands in the collected requests."""
        member = _make_member("notifier-3", SCRIPT_DEFN_FUELED, bus)
        rule = member._spec.rules[0]
        env = member._env({"mail": None, "recipient": member.member_id})
        requests = member._evaluate_rule(rule, env)
        assert [r.kind for r in requests] == ["emit"]
        assert requests[0].data["content"] == "note: hello"

    async def test_defn_helper_on_complete_collector(self):
        """On-complete forms share one env too: a defn defined in the
        first action form is callable in the second, and the body's emit
        is recorded into the collector mid-evaluation."""
        spec = MemberSpec.from_edn(loads(COGNITIVE_DEFN_ACTIONS))
        validate_member_spec(spec)
        rule = spec.rules[0]

        executed: list[ActionRequest] = []

        async def _execute(request: ActionRequest, mail: Mail | None) -> None:
            executed.append(request)

        runner = OnCompleteRunner(
            identity=AgentIdentity(
                id="thinker-9", name="thinker-9", address="thinker-9@tlon.internal"
            ),
            rule=rule,
            logger=get_logger("test"),
            execute_action=_execute,
        )
        session = AgentSession(session_id="s-1", trigger_content="hi")
        await runner.run(session, "the reply")
        assert [r.kind for r in executed] == ["emit"]
        assert executed[0].data["content"] == "done: the reply"


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
        # …but marked read immediately (a self-copy must not linger)
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


# ── Cognitive routing (D6.1 / loop-first-then-actions) ─────────────────


def _cognitive_caps(config: AgentConfig, tlon_paths: TlonPaths) -> CognitiveCapabilities:
    return CognitiveCapabilities(
        config=config,
        llm_client=_StubLLM(),
        memory=_MemoryStub(),
        task_manager=_TaskStub(),
        tool_executor=ToolExecutor(),
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
            tlon_paths=TlonPaths(home=mail_dir.parent),
        )
        member = _make_member("thinker-x", COGNITIVE_EDN, bus, mailbox=mailbox, cognitive=caps)

        await member.shutdown()

        assert recorder.shutdown_called

    async def test_member_shutdown_closes_docker_runtime(self, bus, mail_dir):
        """shutdown closes the assembled DockerRuntime (the client's
        connection pool must not leak across sleep→wake re-assembly)."""
        mailbox = Mailbox(mail_dir)
        config = AgentConfig(id="thinker-y", name="thinker-y", llm=LLMConfig(model_name="test"))
        docker_runtime = MagicMock()
        caps = CognitiveCapabilities(
            config=config,
            llm_client=_StubLLM(),
            memory=_MemoryStub(),
            task_manager=_TaskStub(),
            tool_executor=ToolExecutor(),
            tlon_paths=TlonPaths(home=mail_dir.parent),
            docker_runtime=docker_runtime,
        )
        member = _make_member("thinker-y", COGNITIVE_EDN, bus, mailbox=mailbox, cognitive=caps)

        await member.shutdown()

        docker_runtime.close.assert_called_once()

    async def test_member_shutdown_without_docker_runtime(self, bus, mail_dir):
        """docker_runtime defaults to None (daemon unreachable at assembly,
        or a capability set built without assembly) — shutdown must not
        blow up."""
        mailbox = Mailbox(mail_dir)
        config = AgentConfig(id="thinker-z", name="thinker-z", llm=LLMConfig(model_name="test"))
        caps = CognitiveCapabilities(
            config=config,
            llm_client=_StubLLM(),
            memory=_MemoryStub(),
            task_manager=_TaskStub(),
            tool_executor=ToolExecutor(),
            tlon_paths=TlonPaths(home=mail_dir.parent),
        )
        member = _make_member("thinker-z", COGNITIVE_EDN, bus, mailbox=mailbox, cognitive=caps)

        await member.shutdown()


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


# ── Boot-scan throttling (docs/design/runtime.md) ─────────────────────


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
        root = Mail.create(from_address="alice@tlon.internal", content="first", to=[member.address])
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
        mail = Mail.create(from_address="alice@tlon.internal", content="hello", to=[member.address])
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
            member._rescan_task = asyncio.create_task(member._rescan_loop())
            processed: list[str] = []
            while len(processed) < len(mails):
                for mail in self._drain_queue(member):
                    processed.append(mail.id)
                    await member._mark_read(mail)
                await asyncio.sleep(0.01)
            await _wait_for(lambda: not member._scan_deferred)

        # Every mail was picked up exactly once; the scan went clean and
        # the task left the trickle cadence for the periodic sweep.
        assert sorted(processed) == sorted(m.id for m in mails)
        assert member._scan_deferred is False
        assert await mailbox.unread() == []

        await member.shutdown()


class TestRuntimeThreadMerge:
    """Runtime same-thread merging (ADR 0026): the live delivery path
    merges like the boot scan.

    A delivered mail whose thread already has a pending (enqueued, not
    yet admitted) mail replaces it as the thread's representative — the
    displaced sibling is marked read now, no second session is queued.
    Merging is a replacement, not an addition: a full bounded queue still
    merges same-thread mail and only drops mail of a *new* thread. The
    scheduler is never started here; the tests drive ``_on_event`` and
    the queue directly, the same way TestBootScanThrottle does.
    """

    @staticmethod
    def _thread_mails(member: MemberRuntime, count: int) -> list[Mail]:
        """A chain of ``count`` replies on one thread, latest last."""
        root = Mail.create(
            from_address="alice@tlon.internal", content="turn 0", to=[member.address]
        )
        mails = [root]
        for i in range(1, count):
            mails.append(
                Mail.create(
                    from_address="alice@tlon.internal",
                    content=f"turn {i}",
                    to=[member.address],
                    in_reply_to=mails[-1].id,
                    thread_id=root.thread_id,
                )
            )
        return mails

    async def test_same_thread_pending_mail_replaced_by_latest(self, bus, mail_dir):
        mailbox = Mailbox(mail_dir)
        member = _make_cognitive_member("thinker-m1", COGNITIVE_EDN, bus, mailbox)
        scheduler = member._scheduler
        assert scheduler is not None
        first, second = self._thread_mails(member, 2)

        assert scheduler.enqueue_merging_thread(first, {}) == ("queued", None)
        result, displaced = scheduler.enqueue_merging_thread(second, {})

        assert result == "merged"
        assert displaced is not None and displaced.id == first.id
        assert [mail.id for mail, _ in scheduler._mail_queue._queue] == [second.id]

    async def test_different_threads_do_not_merge(self, bus, mail_dir):
        mailbox = Mailbox(mail_dir)
        member = _make_cognitive_member("thinker-m2", COGNITIVE_EDN, bus, mailbox)
        scheduler = member._scheduler
        assert scheduler is not None
        first, second = self._thread_mails(member, 2)
        other = Mail.create(
            from_address="alice@tlon.internal", content="separate", to=[member.address]
        )

        assert scheduler.enqueue_merging_thread(first, {}) == ("queued", None)
        assert scheduler.enqueue_merging_thread(other, {}) == ("queued", None)
        result, _displaced = scheduler.enqueue_merging_thread(second, {})

        assert result == "merged"
        # The replacement keeps the burst's FIFO slot; the other thread
        # is untouched.
        assert [mail.id for mail, _ in scheduler._mail_queue._queue] == [second.id, other.id]

    async def test_full_queue_still_merges_same_thread(self, bus, mail_dir):
        """A full queue drops only a *new* thread; merging is a
        replacement (one out, one in), so it succeeds regardless."""
        mailbox = Mailbox(mail_dir)
        member = TestBootScanThrottle._make_throttled_member(
            "thinker-m3", bus, mailbox, queue_size=1
        )
        scheduler = member._scheduler
        assert scheduler is not None
        first, second = self._thread_mails(member, 2)
        new_thread = Mail.create(
            from_address="alice@tlon.internal", content="new thread", to=[member.address]
        )

        assert scheduler.enqueue_merging_thread(first, {}) == ("queued", None)
        assert scheduler.enqueue_merging_thread(new_thread, {}) == ("dropped", None)
        result, displaced = scheduler.enqueue_merging_thread(second, {})

        assert result == "merged"
        assert displaced is not None and displaced.id == first.id
        assert [mail.id for mail, _ in scheduler._mail_queue._queue] == [second.id]

    async def test_two_same_thread_deliveries_one_pending_session(self, bus, mail_dir):
        """End to end on the live path: two MAIL_DELIVERED of one thread
        leave one pending session trigger; the displaced mail is marked
        read and logged."""
        mailbox = Mailbox(mail_dir)
        member = _make_cognitive_member("thinker-m4", COGNITIVE_EDN, bus, mailbox)
        first, second = self._thread_mails(member, 2)
        for mail in (first, second):
            await mailbox.append_inbox(mail)

        with patch("tlon.agents.member_runtime.logger") as mock_logger:
            await member._on_event(_deliver(first, member.member_id))
            await member._on_event(_deliver(second, member.member_id))

        merged_events = [
            call.kwargs
            for call in mock_logger.info.call_args_list
            if call.args[0] == "mail_merged"
        ]
        assert len(merged_events) == 1
        assert merged_events[0]["merged_mail_id"] == first.id
        assert merged_events[0]["representative_mail_id"] == second.id
        assert merged_events[0]["thread_id"] == first.thread_id
        # One pending trigger (the latest mail); the displaced one is
        # read, the representative's read defers to session completion.
        assert [mail.id for mail in TestBootScanThrottle._drain_queue(member)] == [second.id]
        assert [m.id for m in await mailbox.unread()] == [second.id]


class TestPeriodicRescan:
    """The one background rescan task also sweeps periodically
    (docs/design/runtime.md).

    A boot member (``:on :boot``) never sleeps and never reboots, so
    live mail the full bounded queue dropped would wait for a process
    restart without the periodic sweep. The same task that trickles
    boot-scan overflow re-scans unread mail every
    ``mail_rescan_interval_seconds``; a sweep over an empty inbox
    enqueues nothing and never touches the LLM.

    The scheduler itself is never started here (its sessions would need
    a real LLM); the tests drive the queue directly, standing in for
    session completions, the same way TestBootScanThrottle does.
    """

    @staticmethod
    def _make_rescan_member(
        member_id: str,
        bus: EventBus,
        mailbox: Mailbox,
        *,
        queue_size: int,
        interval: float,
    ) -> MemberRuntime:
        config = AgentConfig(
            id=member_id,
            name=member_id,
            llm=LLMConfig(model_name="test"),
            message_queue_size=queue_size,
            mail_rescan_interval_seconds=interval,
        )
        return _make_member(
            member_id,
            COGNITIVE_EDN,
            bus,
            mailbox=mailbox,
            cognitive=_cognitive_caps(config, TlonPaths(home=mailbox._dir.parent)),
        )

    async def test_live_dropped_mail_picked_up_by_periodic_rescan(self, bus, mail_dir):
        """A boot member never reboots: the periodic sweep picks up
        live mail the full bounded queue dropped."""
        mailbox = Mailbox(mail_dir)
        member = self._make_rescan_member("resident-1", bus, mailbox, queue_size=1, interval=0.05)
        first = Mail.create(from_address="alice@tlon.internal", content="one", to=[member.address])
        second = Mail.create(from_address="alice@tlon.internal", content="two", to=[member.address])
        await mailbox.append_inbox(first)
        await mailbox.append_inbox(second)

        # Live dispatch: the first fills the bounded queue, the second is
        # dropped and stays unread (live drop semantics unchanged).
        await member._on_event(_deliver(first, member.member_id))
        await member._on_event(_deliver(second, member.member_id))
        assert [m.id for m in await mailbox.unread()] == [first.id, second.id]

        member._initialized = True
        with patch("tlon.agents.member_runtime._RESCAN_POLL_SECONDS", 0.02):
            member._rescan_task = asyncio.create_task(member._rescan_loop())
            # Drain + mark read, standing in for session completions.
            processed: list[str] = []
            while len(processed) < 2:
                for mail in TestBootScanThrottle._drain_queue(member):
                    processed.append(mail.id)
                    await member._mark_read(mail)
                await asyncio.sleep(0.01)

        assert sorted(processed) == sorted([first.id, second.id])
        assert await mailbox.unread() == []

        await member.shutdown()

    async def test_periodic_rescan_without_unread_enqueues_nothing(self, bus, mail_dir):
        """An idle sweep is a no-op: no enqueue, no scan state, no LLM
        call (the stub LLM raises if ever invoked)."""
        mailbox = Mailbox(mail_dir)
        member = self._make_rescan_member("resident-2", bus, mailbox, queue_size=2, interval=0.03)
        member._initialized = True
        member._rescan_task = asyncio.create_task(member._rescan_loop())
        await asyncio.sleep(0.15)  # several sweeps
        await member.shutdown()

        assert TestBootScanThrottle._drain_queue(member) == []
        assert member._scan_pending == set()
        assert member._scan_deferred is False
        assert await mailbox.unread() == []

    async def test_one_task_covers_trickle_and_periodic_sweep(self, bus, mail_dir):
        """No concurrent rescans: one task runs both cadences.

        The deferred trickle drains boot-scan overflow; once the scan
        goes clean the same task keeps sweeping periodically and picks
        up a later live drop — ``_rescan_task`` is never replaced.
        """
        mailbox = Mailbox(mail_dir)
        member = self._make_rescan_member("resident-3", bus, mailbox, queue_size=1, interval=0.05)
        sender = "alice@tlon.internal"
        backlog = [
            Mail.create(from_address=sender, content=f"backlog {i}", to=[member.address])
            for i in range(2)
        ]
        for mail in backlog:
            await mailbox.append_inbox(mail)
        await member._scan_unread_mail()
        assert member._scan_deferred is True

        member._initialized = True
        with patch("tlon.agents.member_runtime._RESCAN_POLL_SECONDS", 0.02):
            task = asyncio.create_task(member._rescan_loop())
            member._rescan_task = task

            # Trickle cadence: drain + read, the overflow follows.
            processed: list[str] = []
            while len(processed) < len(backlog):
                for mail in TestBootScanThrottle._drain_queue(member):
                    processed.append(mail.id)
                    await member._mark_read(mail)
                await asyncio.sleep(0.01)
            await _wait_for(lambda: not member._scan_deferred)
            assert member._rescan_task is task

            # A live drop while the queue is full: the periodic sweep of
            # the same task picks it up (no reboot, no second task).
            live = Mail.create(from_address=sender, content="live", to=[member.address])
            await mailbox.append_inbox(live)
            filler = Mail.create(from_address=sender, content="filler", to=[member.address])
            assert member._scheduler is not None
            assert member._scheduler.enqueue(filler, {})
            await member._on_event(_deliver(live, member.member_id))
            assert [m.id for m in await mailbox.unread()] == [live.id]

            while live.id not in processed:
                for mail in TestBootScanThrottle._drain_queue(member):
                    if mail.id != filler.id:
                        processed.append(mail.id)
                    await member._mark_read(mail)
                await asyncio.sleep(0.01)
            assert member._rescan_task is task

        await member.shutdown()
        # Shutdown cancelled the one rescan task; nothing else was ever
        # created.
        assert member._rescan_task is None
        assert sorted(processed) == sorted([m.id for m in backlog] + [live.id])
        assert await mailbox.unread() == []


# ── Idle-aware background tasks (docs/design/runtime.md) ─────────────

COGNITIVE_REFLECT_EDN = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :when (addressed-to-me? msg)
          :loop {:context {:sections [(persona) (protocol)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :max-tokens 1000000
                 :reflect {:enabled true :interval-minutes 0}}}]}
"""


class _BlockingLLM:
    """Duck-typed LLMClient whose chat blocks until released."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat(self, **kwargs: Any) -> Any:
        self.started.set()
        await self.release.wait()
        return LLMResponse(content='{"operations": []}')


class _DigestMemoryStub(_MemoryStub):
    """Memory stub with one claim, so a reflection proceeds to the LLM call."""

    async def get_memory_digest(self, logs: Any) -> dict[str, Any]:
        return {
            "undistilled_session_count": 0,
            "claim_count": 1,
            "claims": [{"id": "claim-1", "text": "something", "timestamp": "2026-09-17"}],
        }


class TestIdleAwareBackground:
    """Background activity feeds the idle accounting; shutdown cancels it
    (docs/design/runtime.md).

    An in-flight reflection (memory digest + LLM audit) is real work:
    ``idle_seconds`` must not report the member idle mid-audit, and
    shutdown must cancel the reflection task instead of leaving a
    free-floating coroutine. The resident rescan task, by contrast,
    sleeps between sweeps — its mere existence never counts as activity.
    """

    async def test_in_flight_reflection_counts_as_active(self, bus, mail_dir):
        mailbox = Mailbox(mail_dir)
        member = _make_cognitive_member("thinker-i1", COGNITIVE_EDN, bus, mailbox)
        scheduler = member._scheduler
        reflection = member._reflection
        assert scheduler is not None and reflection is not None

        # No sessions for a long while: the member reads idle.
        scheduler._last_activity_at = datetime.now(UTC) - timedelta(seconds=999)
        assert member.idle_seconds > 600

        # An in-flight reflection is real activity — not idle.
        reflection._running = True
        assert member.idle_seconds == 0.0

        reflection._running = False
        assert member.idle_seconds > 600

    async def test_shutdown_cancels_in_flight_reflection(self, bus, mail_dir):
        mailbox = Mailbox(mail_dir)
        config = AgentConfig(id="thinker-i2", name="thinker-i2", llm=LLMConfig(model_name="test"))
        llm = _BlockingLLM()
        caps = CognitiveCapabilities(
            config=config,
            llm_client=llm,
            memory=_DigestMemoryStub(),
            task_manager=_TaskStub(),
            tool_executor=ToolExecutor(),
            tlon_paths=TlonPaths(home=mail_dir.parent),
        )
        member = _make_member(
            "thinker-i2", COGNITIVE_REFLECT_EDN, bus, mailbox=mailbox, cognitive=caps
        )
        reflection = member._reflection
        assert reflection is not None

        for _ in range(5):  # reflection_min_interactions default
            reflection.note_interaction()
        await reflection.check_trigger()
        await asyncio.wait_for(llm.started.wait(), timeout=2)
        assert reflection.is_running
        assert member.idle_seconds == 0.0

        task = reflection._task
        assert task is not None
        await member.shutdown()

        assert task.cancelled()
        assert reflection._task is None
        assert not reflection.is_running


class TestMentorCc:
    """Trust ladder (): a probationary member's mentor is cc'd on
    composed outbound mail (_send_mail — the emit/completion path);
    forwarding is NOT cc'd (passing along an existing mail is noise)."""

    MENTOR = "mentor@tlon.internal"

    async def test_send_mail_cc_injected(self, bus):
        member = _make_member("rookie-1", SCRIPT_EMIT, bus)
        member._mentor_cc = self.MENTOR
        await member.initialize()
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, sent))

        await member._send_mail(to=["user@tlon.internal"], content="进度汇报")

        await _wait_for(lambda: len(sent) == 1)
        assert sent[0].payload.cc == [self.MENTOR]

        await member.shutdown()

    async def test_send_mail_mentor_already_recipient_not_duplicated(self, bus):
        member = _make_member("rookie-1", SCRIPT_EMIT, bus)
        member._mentor_cc = self.MENTOR
        await member.initialize()
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, sent))

        await member._send_mail(to=[self.MENTOR], content="给 mentor 本人")

        await _wait_for(lambda: len(sent) == 1)
        assert sent[0].payload.cc == []

        await member.shutdown()

    async def test_send_mail_mentor_already_in_cc_not_duplicated(self, bus):
        member = _make_member("rookie-1", SCRIPT_EMIT, bus)
        member._mentor_cc = self.MENTOR
        await member.initialize()
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, sent))

        await member._send_mail(
            to=["user@tlon.internal"], content="x", cc=[self.MENTOR, "other@tlon.internal"]
        )

        await _wait_for(lambda: len(sent) == 1)
        assert sent[0].payload.cc == [self.MENTOR, "other@tlon.internal"]

        await member.shutdown()

    async def test_forward_not_cced(self, bus, mail_dir):
        """Forward passes along an existing mail — cc'ing the mentor on
        every relay would be noise, so it is deliberately exempt."""
        mailbox = Mailbox(mail_dir)
        member = _make_member("rookie-1", SCRIPT_FORWARD, bus, mailbox=mailbox)
        member._mentor_cc = self.MENTOR
        await member.initialize()
        sent: list[Event] = []
        bus.subscribe(EventType.MAIL_NEW, partial(_capture, sent))

        original = Mail.create(
            from_address="alice@tlon.internal", content="hello", to=[member.address]
        )
        await mailbox.append_inbox(original)
        await bus.publish(_deliver(original, member.member_id))

        await _wait_for(lambda: len(sent) == 1)
        assert sent[0].payload.forward_of == original.id
        assert sent[0].payload.cc == []

        await member.shutdown()
