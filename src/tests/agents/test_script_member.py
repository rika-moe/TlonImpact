"""Tests for ScriptMember (tlon.agents.script_member) — the generic
policy-as-data runtime (P5a/P5b).

Covers subscription dedup, boot rules running exactly once, ``:when``
gating, trace-carrier passthrough, and EvalError isolation (one failing
rule never affects other rules or the bus dispatch) — plus the P5b mail
semantics: forward execution, the own-mail guard, the unread rescue scan
with read-marking, and idle bookkeeping.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.agents.edn import loads
from tlon.agents.eval import make_script_builtins
from tlon.agents.lifecycle import StartRequest, StopRequest
from tlon.agents.script_member import ScriptMember
from tlon.agents.script_spec import ScriptSpec
from tlon.base.events.event import Event, EventType
from tlon.organization.mail.mail import Mail

TRACE_ID = "00000000000000000000000000000001"
SPAN_ID = "0000000000000001"

RELAY_SPEC = """\
{:version 1
 :rules [{:name "relay"
          :on :mail-delivered
          :actions [(forward {:to ["gu-yanxi@tlon.internal"]})]}]}
"""


class _FakeHost:
    def __init__(self, sleeping: set[str] | None = None, candidates: list[str] | None = None):
        self._sleeping = sleeping or set()
        self._candidates = candidates or []

    def is_sleeping(self, agent_id: Any) -> bool:
        return agent_id in self._sleeping

    def is_running(self, agent_id: Any) -> bool:
        return isinstance(agent_id, str) and agent_id not in self._sleeping

    def is_lazy(self, agent_id: Any) -> bool:
        return False

    def start_all_candidates(self) -> list[str]:
        return list(self._candidates)


class _FakeMailbox:
    """Duck-typed Mailbox: unread list + recorded mark_read calls."""

    def __init__(self, unread: list[Mail] | None = None):
        self._unread = unread or []
        self.mark_read = AsyncMock()

    async def unread(self) -> list[Mail]:
        return list(self._unread)


def _make_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = AsyncMock()
    bus.subscribe = MagicMock(side_effect=lambda *_args, **_kwargs: "sub-x")
    bus.unsubscribe = MagicMock()
    return bus


def _make_member(
    source: str,
    bus: MagicMock,
    host: _FakeHost | None = None,
    member_id: str = "script-001",
    mailbox: _FakeMailbox | None = None,
) -> ScriptMember:
    builtins = make_script_builtins(host)
    spec = ScriptSpec.from_edn(loads(source), builtins=builtins)
    return ScriptMember(
        member_id=member_id, event_bus=bus, spec=spec, builtins=builtins, mailbox=mailbox
    )


def _make_mail(
    from_address: str = "xie-yunshu@tlon.internal",
    content: str = "原件内容",
    to: list[str] | None = None,
) -> Mail:
    return Mail.create(
        from_address=from_address,
        to=to or ["script-001@tlon.internal"],
        content=content,
    )


def _mail_event(mail: Mail, recipient: str = "script-001", **kwargs: Any) -> Event:
    return Event(
        type=EventType.MAIL_DELIVERED,
        payload={"mail": mail, "recipient": recipient},
        source="mail_delivery",
        **kwargs,
    )


def _published(bus: MagicMock, event_type: EventType) -> list[Event]:
    return [
        call.args[0]
        for call in bus.publish.await_args_list
        if call.args[0].type == event_type
    ]


class TestIdentity:
    def test_member_identity(self):
        member = _make_member(
            '{:version 1 :rules [{:name "r" :on :agent-idle '
            ':actions [(stop {:agent "a" :reason "drain"})]}]}',
            _make_bus(),
        )
        assert member.member_id == "script-001"
        assert member.member_type == "script"
        assert member.get_state() == "offline"


class TestSubscription:
    @pytest.mark.asyncio
    async def test_event_types_subscribed_once(self):
        """Two rules on the same event type share one subscription."""
        bus = _make_bus()
        member = _make_member(
            '{:version 1 :rules ['
            '{:name "r1" :on :mail-delivered '
            ':actions [(start {:agent "a" :reason "wake"})]} '
            '{:name "r2" :on :mail-delivered '
            ':actions [(start {:agent "b" :reason "wake"})]} '
            '{:name "r3" :on :agent-idle '
            ':actions [(stop {:agent "c" :reason "drain"})]}]}',
            bus,
        )
        await member.initialize()

        subscribed = sorted(call.args[0] for call in bus.subscribe.call_args_list)
        assert subscribed == sorted([EventType.MAIL_DELIVERED, EventType.AGENT_IDLE])

    @pytest.mark.asyncio
    async def test_shutdown_unsubscribes(self):
        bus = _make_bus()
        member = _make_member(
            '{:version 1 :rules [{:name "r" :on :agent-idle '
            ':actions [(stop {:agent "a" :reason "drain"})]}]}',
            bus,
        )
        await member.initialize()
        assert member.get_state() == "active"

        await member.shutdown()

        assert bus.unsubscribe.call_count == 1
        assert member.get_state() == "offline"


class TestBootRules:
    @pytest.mark.asyncio
    async def test_boot_rule_runs_once_at_initialize(self):
        bus = _make_bus()
        host = _FakeHost(candidates=["a-001", "a-002"])
        member = _make_member(
            '{:version 1 :rules [{:name "boot" :on :boot '
            ':actions [(start-all {:reason "boot"})]}]}',
            bus,
            host=host,
        )
        await member.initialize()

        requests = _published(bus, EventType.AGENT_START_REQUEST)
        assert len(requests) == 2
        assert [r.payload.agent_id for r in requests] == ["a-001", "a-002"]
        assert all(isinstance(r.payload, StartRequest) for r in requests)
        assert all(r.payload.reason == "boot" for r in requests)
        # boot decisions carry no trace carrier
        assert all(r.trace_id is None for r in requests)

    @pytest.mark.asyncio
    async def test_boot_rules_run_after_subscriptions(self):
        bus = _make_bus()
        host = _FakeHost(candidates=["a-001"])
        member = _make_member(
            '{:version 1 :rules ['
            '{:name "w" :on :mail-delivered '
            ':actions [(start {:agent "a" :reason "wake"})]} '
            '{:name "boot" :on :boot :actions [(start-all {:reason "boot"})]}]}',
            bus,
            host=host,
        )
        await member.initialize()
        assert bus.subscribe.call_count == 1  # subscription already in place
        assert len(_published(bus, EventType.AGENT_START_REQUEST)) == 1


class TestWhenGating:
    @pytest.mark.asyncio
    async def test_when_false_skips_actions(self):
        bus = _make_bus()
        host = _FakeHost(sleeping=set())  # nobody sleeping
        member = _make_member(
            '{:version 1 :rules [{:name "wake" :on :mail-delivered '
            ':when (sleeping? (get event :recipient)) '
            ':actions [(start {:agent (get event :recipient) :reason "wake"})]}]}',
            bus,
            host=host,
        )
        await member.initialize()

        await member._on_event(
            Event(
                type=EventType.MAIL_DELIVERED,
                payload={"recipient": "a-001"},
                source="mail_delivery",
            )
        )

        assert _published(bus, EventType.AGENT_START_REQUEST) == []

    @pytest.mark.asyncio
    async def test_when_true_runs_actions(self):
        bus = _make_bus()
        host = _FakeHost(sleeping={"a-001"})
        member = _make_member(
            '{:version 1 :rules [{:name "wake" :on :mail-delivered '
            ':when (sleeping? (get event :recipient)) '
            ':actions [(start {:agent (get event :recipient) :reason "wake"})]}]}',
            bus,
            host=host,
        )
        await member.initialize()

        await member._on_event(
            Event(
                type=EventType.MAIL_DELIVERED,
                payload={"recipient": "a-001"},
                source="mail_delivery",
            )
        )

        requests = _published(bus, EventType.AGENT_START_REQUEST)
        assert len(requests) == 1
        assert requests[0].payload.agent_id == "a-001"
        assert requests[0].payload.reason == "wake"


class TestTracePassthrough:
    @pytest.mark.asyncio
    async def test_decision_joins_triggering_events_trace_tree(self):
        bus = _make_bus()
        member = _make_member(
            '{:version 1 :rules [{:name "wake" :on :mail-delivered '
            ':actions [(start {:agent (get event :recipient) :reason "wake"})]}]}',
            bus,
        )
        await member.initialize()

        await member._on_event(
            Event(
                type=EventType.MAIL_DELIVERED,
                payload={"recipient": "a-001"},
                source="mail_delivery",
                trace_id=TRACE_ID,
                span_id=SPAN_ID,
            )
        )

        requests = _published(bus, EventType.AGENT_START_REQUEST)
        assert len(requests) == 1
        assert requests[0].trace_id == TRACE_ID
        assert requests[0].span_id == SPAN_ID


class TestEvalErrorIsolation:
    @pytest.mark.asyncio
    async def test_failing_rule_skipped_other_rules_unaffected(self):
        """An EvalError skips the rule with a warning; a sibling rule on the
        same event still runs, and nothing escapes to the bus dispatch."""
        bus = _make_bus()
        member = _make_member(
            '{:version 1 :rules ['
            # r1: :agent evaluates to None -> start validation fails at runtime
            '{:name "bad" :on :agent-idle '
            ':actions [(start {:agent (get event :missing) :reason "wake"})]} '
            '{:name "good" :on :agent-idle '
            ':actions [(stop {:agent "a-001" :reason "drain"})]}]}',
            bus,
        )
        await member.initialize()

        await member._on_event(
            Event(
                type=EventType.AGENT_IDLE,
                payload={"agent_id": "a-001"},
                source="agent_registry",
            )
        )

        assert _published(bus, EventType.AGENT_START_REQUEST) == []
        stops = _published(bus, EventType.AGENT_STOP_REQUEST)
        assert len(stops) == 1
        assert isinstance(stops[0].payload, StopRequest)
        assert stops[0].payload.agent_id == "a-001"

    @pytest.mark.asyncio
    async def test_failing_when_gate_skips_rule(self):
        bus = _make_bus()
        member = _make_member(
            '{:version 1 :rules [{:name "r" :on :mail-delivered '
            ":when (contains? (get event :recipient) 42) "
            ':actions [(start {:agent "a-001" :reason "wake"})]}]}',
            bus,
        )
        await member.initialize()

        await member._on_event(
            Event(
                type=EventType.MAIL_DELIVERED,
                payload={"recipient": None},
                source="mail_delivery",
            )
        )

        assert _published(bus, EventType.AGENT_START_REQUEST) == []

    @pytest.mark.asyncio
    async def test_event_of_other_type_triggers_no_rule(self):
        bus = _make_bus()
        member = _make_member(
            '{:version 1 :rules [{:name "r" :on :agent-idle '
            ':actions [(stop {:agent "a" :reason "drain"})]}]}',
            bus,
        )
        await member.initialize()

        await member._on_event(
            Event(type=EventType.MAIL_DELIVERED, payload={"recipient": "a"}, source="x")
        )

        bus.publish.assert_not_called()


class TestForward:
    """P5b: the forward action executed natively by the runtime."""

    @pytest.mark.asyncio
    async def test_forward_publishes_mail_new(self):
        bus = _make_bus()
        member = _make_member(RELAY_SPEC, bus)
        await member.initialize()
        original = _make_mail()

        await member._on_event(_mail_event(original))

        published = _published(bus, EventType.MAIL_NEW)
        assert len(published) == 1
        forwarded = published[0].payload
        assert isinstance(forwarded, Mail)
        assert forwarded.id != original.id
        assert forwarded.from_address == "script-001@tlon.internal"
        assert forwarded.to == ["gu-yanxi@tlon.internal"]
        assert forwarded.cc == []
        assert forwarded.subject is None
        assert forwarded.content == original.content
        assert forwarded.forward_of == original.id
        assert published[0].source == "script-001"

    @pytest.mark.asyncio
    async def test_forward_joins_triggering_events_trace_tree(self):
        bus = _make_bus()
        member = _make_member(RELAY_SPEC, bus)
        await member.initialize()

        await member._on_event(
            _mail_event(_make_mail(), trace_id=TRACE_ID, span_id=SPAN_ID)
        )

        published = _published(bus, EventType.MAIL_NEW)
        assert len(published) == 1
        assert published[0].trace_id == TRACE_ID
        assert published[0].span_id == SPAN_ID

    @pytest.mark.asyncio
    async def test_forward_without_mail_in_trigger_skips(self):
        """A forward rule triggered by a mail-less event warns and skips."""
        bus = _make_bus()
        member = _make_member(RELAY_SPEC, bus)
        await member.initialize()

        await member._on_event(
            Event(
                type=EventType.MAIL_DELIVERED,
                payload={"recipient": "script-001"},
                source="mail_delivery",
            )
        )

        assert _published(bus, EventType.MAIL_NEW) == []

    @pytest.mark.asyncio
    async def test_forward_with_invalid_address_skips(self):
        """A malformed literal address in the spec fails the rule only."""
        bus = _make_bus()
        member = _make_member(
            '{:version 1 :rules [{:name "relay" :on :mail-delivered '
            ':actions [(forward {:to ["not-an-address"]})]}]}',
            bus,
        )
        await member.initialize()

        await member._on_event(_mail_event(_make_mail()))

        assert _published(bus, EventType.MAIL_NEW) == []


class TestOwnMailGuard:
    """P5b: mail from the member itself is ignored (no relay loops)."""

    @pytest.mark.asyncio
    async def test_own_mail_triggers_no_rule(self):
        bus = _make_bus()
        mailbox = _FakeMailbox()
        member = _make_member(RELAY_SPEC, bus, mailbox=mailbox)
        await member.initialize()
        own = _make_mail(from_address="script-001@tlon.internal")

        await member._on_event(_mail_event(own))

        assert _published(bus, EventType.MAIL_NEW) == []
        # ...but it is still marked read, so the boot scan never replays it
        mailbox.mark_read.assert_awaited_once_with(own.id)

    @pytest.mark.asyncio
    async def test_own_mail_for_other_recipient_not_marked(self):
        bus = _make_bus()
        mailbox = _FakeMailbox()
        member = _make_member(RELAY_SPEC, bus, mailbox=mailbox)
        await member.initialize()
        own = _make_mail(
            from_address="script-001@tlon.internal", to=["someone@tlon.internal"]
        )

        await member._on_event(_mail_event(own, recipient="someone"))

        mailbox.mark_read.assert_not_awaited()


class TestUnreadScan:
    """P5b: the boot unread scan replays mail that arrived while sleeping."""

    @pytest.mark.asyncio
    async def test_unread_mail_forwarded_and_marked_read(self):
        bus = _make_bus()
        mails = [_make_mail(content="第一封"), _make_mail(content="第二封")]
        mailbox = _FakeMailbox(unread=mails)
        member = _make_member(RELAY_SPEC, bus, mailbox=mailbox)

        await member.initialize()

        published = _published(bus, EventType.MAIL_NEW)
        assert len(published) == 2
        assert [event.payload.content for event in published] == ["第一封", "第二封"]
        assert all(
            event.payload.forward_of == mail.id
            for event, mail in zip(published, mails, strict=True)
        )
        # The scan carries no trace carrier (no triggering event)
        assert all(event.trace_id is None for event in published)
        assert mailbox.mark_read.await_count == 2
        for mail in mails:
            mailbox.mark_read.assert_any_await(mail.id)

    @pytest.mark.asyncio
    async def test_own_mail_in_inbox_marked_read_without_forward(self):
        bus = _make_bus()
        own = _make_mail(from_address="script-001@tlon.internal")
        mailbox = _FakeMailbox(unread=[own])
        member = _make_member(RELAY_SPEC, bus, mailbox=mailbox)

        await member.initialize()

        assert _published(bus, EventType.MAIL_NEW) == []
        mailbox.mark_read.assert_awaited_once_with(own.id)

    @pytest.mark.asyncio
    async def test_realtime_mail_marked_read_after_processing(self):
        bus = _make_bus()
        mailbox = _FakeMailbox()
        member = _make_member(RELAY_SPEC, bus, mailbox=mailbox)
        await member.initialize()
        mail = _make_mail()

        await member._on_event(_mail_event(mail))

        mailbox.mark_read.assert_awaited_once_with(mail.id)

    @pytest.mark.asyncio
    async def test_mail_for_other_recipient_not_marked(self):
        """The member sees every MAIL_DELIVERED (rules may fire on others'
        mail, like Adam's wake) but only marks its own copy read."""
        bus = _make_bus()
        mailbox = _FakeMailbox()
        member = _make_member(RELAY_SPEC, bus, mailbox=mailbox)
        await member.initialize()

        await member._on_event(_mail_event(_make_mail(), recipient="someone-else"))

        mailbox.mark_read.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mark_read_failure_is_contained(self):
        bus = _make_bus()
        mailbox = _FakeMailbox(unread=[_make_mail()])
        mailbox.mark_read = AsyncMock(side_effect=OSError("disk gone"))
        member = _make_member(RELAY_SPEC, bus, mailbox=mailbox)

        await member.initialize()  # must not raise

        assert len(_published(bus, EventType.MAIL_NEW)) == 1


class TestIdleSemantics:
    """P5b: registry drain reads active_session_count / idle_seconds."""

    def test_no_sessions_ever(self):
        member = _make_member(RELAY_SPEC, _make_bus())
        assert member.active_session_count == 0

    def test_idle_is_zero_before_initialize(self):
        member = _make_member(RELAY_SPEC, _make_bus())
        assert member.idle_seconds == 0.0

    @pytest.mark.asyncio
    async def test_idle_counts_from_initialize_without_actions(self):
        bus = _make_bus()
        member = _make_member(RELAY_SPEC, bus)
        await member.initialize()

        assert member.idle_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_action_execution_resets_idle_clock(self):
        bus = _make_bus()
        member = _make_member(RELAY_SPEC, bus)
        await member.initialize()
        member._initialized_at -= 100.0  # pretend it booted long ago
        assert member.idle_seconds >= 100.0

        await member._on_event(_mail_event(_make_mail()))

        assert member.idle_seconds < 100.0
