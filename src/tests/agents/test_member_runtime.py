"""Unified rule execution: ordering, explicit sessions and durable mail claims."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.mail_helpers import make_office
from tlon.agents.member_runtime import MemberRuntime
from tlon.agents.member_spec import MemberSpec
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlosure.edn import loads

RECIPE = """{:context {:sections [(persona)]} :stages [(think) (act)]
:until (iteration-limit-reached?) :max-iterations 2 :max-consecutive-errors 2
:max-tokens 10000 :reflect {:enabled false :interval-minutes 5}}"""


def _spec(actions: str, event: str = "mail-delivered", extra: str = "") -> MemberSpec:
    return MemberSpec.from_edn(
        loads(f'{{:version 1 :rules [{{:name "main" :on :{event} :actions [{actions}]}} {extra}]}}')
    )


def _member(tmp_path: Path, spec: MemberSpec, registry: Any = None) -> MemberRuntime:
    bus = EventBus()
    office = make_office(bus, tmp_path, ["member", "sender", "sink"])
    return MemberRuntime(
        member_id="member",
        name="Member",
        event_bus=bus,
        spec=spec,
        builtins=make_member_builtins(),
        mailbox=Mailbox(tmp_path / "shared/mail.sqlite3", "member"),
        post_office=office,
        registry=registry,
        tlon_paths=TlonPaths(home=tmp_path),
    )


def _mail() -> Mail:
    return Mail.create(
        from_address="sender@tlon.internal", to=["member@tlon.internal"], content="投稿"
    )


def _event(mail: Mail) -> Event:
    return Event(
        type=EventType.MAIL_DELIVERED, payload={"mail": mail, "recipient": "member"}, source="test"
    )


async def _drain(member: MemberRuntime) -> None:
    await asyncio.wait_for(member._queue.join(), 2)


async def test_plain_loop_uses_no_session(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec('(map n (emit (str n) ["sink@tlon.internal"]) [1 2 3])'))
    member._execute_action = AsyncMock()
    await member.initialize()
    try:
        await member._on_event(_event(_mail()))
        await _drain(member)
        assert [
            call.args[0].data["content"] for call in member._execute_action.await_args_list
        ] == ["1", "2", "3"]
        assert member._scheduler is None
    finally:
        await member.shutdown()


async def test_condition_can_request_session_without_outer_loop(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec(f"(if true (run-loop (quote {RECIPE})) nil)"))
    member._run_session_effect = AsyncMock()
    await member.initialize()
    try:
        await member._on_event(_event(_mail()))
        await _drain(member)
        member._run_session_effect.assert_awaited_once()
        assert member._run_session_effect.await_args.args[0].kind == "run_loop"
    finally:
        await member.shutdown()


async def test_non_mail_event_runs_all_rules_in_order(tmp_path: Path) -> None:
    extra = f'{{:name "second" :on :agent-idle :actions [(run-loop (quote {RECIPE}))]}}'
    member = _member(tmp_path, _spec(f"(run-loop (quote {RECIPE}))", "agent-idle", extra))
    member._run_session_effect = AsyncMock()
    await member.initialize()
    try:
        await member._on_event(
            Event(type=EventType.AGENT_IDLE, payload={"agent_id": "x"}, source="test")
        )
        await _drain(member)
        assert [call.args[1] for call in member._run_session_effect.await_args_list] == [
            "main",
            "second",
        ]
    finally:
        await member.shutdown()


async def test_following_forward_keeps_original_mail(tmp_path: Path) -> None:
    member = _member(
        tmp_path, _spec(f'(run-loop (quote {RECIPE})) (forward {{:to ["sink@tlon.internal"]}})')
    )
    order: list[str] = []
    release = asyncio.Event()

    async def run(*args: Any) -> None:
        order.append("session")
        await release.wait()

    member._run_session_effect = run
    member._execute_action = AsyncMock(side_effect=lambda *args, **kwargs: order.append("forward"))
    await member.initialize()
    mail = _mail()
    try:
        await member._on_event(_event(mail))
        while not order:
            await asyncio.sleep(0)
        assert order == ["session"]
        assert member.get_state() == "active"
        release.set()
        await _drain(member)
        assert order == ["session", "forward"]
        assert member._execute_action.await_args.kwargs["session_mail"] is mail
    finally:
        release.set()
        await member.shutdown()


async def test_scan_and_delivery_deduplicate_and_preserve_unread(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec('(emit "hello" ["sink@tlon.internal"])'))
    member._execute_action = AsyncMock()
    mail = _mail()
    await member._mailbox.append_inbox(mail)
    await member.initialize()
    try:
        await member._on_event(_event(mail))
        await _drain(member)
        await member._scan_unread_mail()
        await _drain(member)
        member._execute_action.assert_awaited_once()
        assert [item.id for item in await member._mailbox.unread()] == [mail.id]
    finally:
        await member.shutdown()
    restored = _member(tmp_path, member.spec)
    restored._execute_action = AsyncMock()
    await restored.initialize()
    try:
        await _drain(restored)
        restored._execute_action.assert_not_awaited()
        assert len(await restored._mailbox.unread()) == 1
    finally:
        await restored.shutdown()


async def test_mark_read_is_explicit_effect(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec("(mark-read)"))
    mail = _mail()
    await member._mailbox.append_inbox(mail)
    await member.initialize()
    try:
        await _drain(member)
        assert await member._mailbox.unread() == []
    finally:
        await member.shutdown()


@pytest.mark.parametrize("allowed", [False, True])
async def test_state_gate_covers_all_rule_effects(tmp_path: Path, allowed: bool) -> None:
    registry = MagicMock()
    registry.can_schedule.return_value = allowed
    registry.publish_scheduling_state = AsyncMock()
    member = _member(tmp_path, _spec('(emit "x" ["sink@tlon.internal"])'), registry)
    member._execute_action = AsyncMock()
    await member.initialize()
    try:
        await member._on_event(_event(_mail()))
        await _drain(member)
        assert member._execute_action.await_count == int(allowed)
    finally:
        await member.shutdown()


async def test_distinct_deliveries_execute_serially(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec('(emit "x" ["sink@tlon.internal"])'))
    active = 0
    peak = 0

    async def execute(*args: Any, **kwargs: Any) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    member._execute_action = execute
    await member.initialize()
    try:
        await asyncio.gather(*(member._on_event(_event(_mail())) for _ in range(5)))
        await _drain(member)
        assert peak == 1
    finally:
        await member.shutdown()


async def test_shared_rule_fuel_and_definitions(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec('(defn f [x] (emit x ["sink@tlon.internal"])) (f "done")'))
    requests = member._evaluate_rule(member.spec.rules[0], member._env(None))
    assert len(requests) == 1 and requests[0].data["content"] == "done"


async def test_inflight_scan_cannot_duplicate_and_shutdown_cancels_work(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec('(emit "x" ["sink@tlon.internal"])'))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def execute(*args: Any, **kwargs: Any) -> None:
        entered.set()
        await release.wait()

    member._execute_action = AsyncMock(side_effect=execute)
    mail = _mail()
    await member._mailbox.append_inbox(mail)
    await member.initialize()
    await entered.wait()
    await member._scan_unread_mail()
    await member._on_event(_event(mail))
    assert member._queue.qsize() == 0
    assert member.active_session_count > 0
    await member.shutdown()
    assert member._program_task is None
    assert member.active_session_count == 0


async def test_nested_loops_keep_all_effects(tmp_path: Path) -> None:
    member = _member(
        tmp_path, _spec('(map x (map y (emit (str x y) ["sink@tlon.internal"]) [3 4]) [1 2])')
    )
    requests = member._evaluate_rule(member.spec.rules[0], member._env(None))
    assert [request.data["content"] for request in requests] == ["13", "14", "23", "24"]


async def test_real_forward_preserves_mail_reference_and_read_state(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec('(forward {:to ["sink@tlon.internal"]})'))
    mail = _mail()
    await member._mailbox.append_inbox(mail)
    await member.initialize()
    try:
        await _drain(member)
        delivered = await Mailbox(tmp_path / "shared/mail.sqlite3", "sink").inbox()
        assert len(delivered) == 1
        assert delivered[0].forward_of == mail.id
        assert delivered[0].content == mail.content
        assert [item.id for item in await member._mailbox.unread()] == [mail.id]
    finally:
        await member.shutdown()


async def test_boot_action_and_reply_keep_common_executor(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec('(emit "boot" ["sink@tlon.internal"])', "boot"))
    member._execute_action = AsyncMock()
    await member.initialize()
    try:
        member._execute_action.assert_awaited_once()
        assert member._execute_action.await_args.args[0].data["content"] == "boot"
    finally:
        await member.shutdown()


async def test_own_mail_is_not_processed(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec('(emit "x" ["sink@tlon.internal"])'))
    member._execute_action = AsyncMock()
    mail = Mail.create(from_address=member.address, to=[member.address], content="self")
    await member._mailbox.append_inbox(mail)
    await member.initialize()
    try:
        await _drain(member)
        member._execute_action.assert_not_awaited()
        assert await member._mailbox.unread() == []
    finally:
        await member.shutdown()


async def test_rule_gate_has_no_implicit_read_effect(tmp_path: Path) -> None:
    spec = MemberSpec.from_edn(
        loads(
            '{:version 1 :rules [{:name "reject" :on :mail-delivered :when (not true) :actions [(emit "x" ["sink@tlon.internal"])]}]}'
        )
    )
    member = _member(tmp_path, spec)
    member._execute_action = AsyncMock()
    mail = _mail()
    await member._mailbox.append_inbox(mail)
    await member.initialize()
    try:
        await _drain(member)
        member._execute_action.assert_not_awaited()
        assert len(await member._mailbox.unread()) == 1
        await member._scan_unread_mail()
        assert member._queue.qsize() == 0
    finally:
        await member.shutdown()


async def test_rule_fuel_is_shared_by_gate_and_actions(tmp_path: Path) -> None:
    from tlosure.builtins import FuelExhaustedError

    spec = MemberSpec.from_edn(
        loads(
            '{:version 1 :rules [{:name "budget" :on :mail-delivered :fuel 30 :actions [(while [n 0] (< n 1000) (recur (inc n)))]}]}'
        )
    )
    member = _member(tmp_path, spec)
    with pytest.raises(FuelExhaustedError):
        member._evaluate_rule(spec.rules[0], member._env(None))


async def test_real_session_then_forward_uses_same_trigger(tmp_path: Path) -> None:
    from tlon.agents.member_runtime import MemberCapabilities
    from tlon.agents.tool_executor import ToolExecutor
    from tlon.base.config import AgentConfig, LLMConfig
    from tlon.base.llm_client import LLMResponse, ToolCall
    from tlon.memory.models import MemoryConfig
    from tlon.tools.agents.scheduling import FinishTurnTool

    shell = _member(
        tmp_path, _spec(f'(run-loop (quote {RECIPE})) (forward {{:to ["sink@tlon.internal"]}})')
    )
    llm = MagicMock()
    llm.config = LLMConfig(model_name="openai/gpt-4o-mini")
    llm.chat = AsyncMock(
        return_value=LLMResponse(
            content="", tool_calls=[ToolCall(id="finish", function_name="finishTurn", arguments={})]
        )
    )
    memory = MagicMock()
    memory.config = MemoryConfig()
    memory.load = AsyncMock()
    memory.maybe_distill_completed_sessions = AsyncMock(return_value=0)
    tools = ToolExecutor()
    tools.register_tool(FinishTurnTool())
    caps = MemberCapabilities(
        config=AgentConfig(id="member", name="Member"),
        llm_client=llm,
        memory=memory,
        task_manager=MagicMock(),
        tool_executor=tools,
        tlon_paths=TlonPaths(home=tmp_path),
    )
    member = MemberRuntime(
        member_id="member",
        name="Member",
        event_bus=shell._event_bus,
        spec=shell.spec,
        builtins=make_member_builtins(),
        mailbox=shell._mailbox,
        post_office=shell._post_office,
        capabilities=caps,
    )
    mail = _mail()
    await member._mailbox.append_inbox(mail)
    await member._event_bus.initialize()
    await member.initialize()
    try:
        await _drain(member)
        llm.chat.assert_awaited_once()
        delivered = await Mailbox(tmp_path / "shared/mail.sqlite3", "sink").inbox()
        assert len(delivered) == 1 and delivered[0].forward_of == mail.id
        assert delivered[0].content == mail.content
        assert len(await member._mailbox.unread()) == 1
        sessions = await member._journal.list_completed()
        assert len(sessions) == 1
        replay = await member._journal.resume(sessions[0])
        assert replay.source_mail_id == mail.id
        assert replay.invocation["rule_name"] == "main"
        assert replay.recipe_source
    finally:
        await member.shutdown()
        await member._event_bus.shutdown()


async def test_while_effects_deliver_two_real_mails(tmp_path: Path) -> None:
    member = _member(
        tmp_path,
        _spec('(while [i 0] (< i 2) (emit (str i) ["sink@tlon.internal"]) (recur (inc i)))'),
    )
    await member._event_bus.initialize()
    await member.initialize()
    try:
        await member._on_event(_event(_mail()))
        await _drain(member)
        delivered = await Mailbox(tmp_path / "shared/mail.sqlite3", "sink").inbox()
        assert [mail.content for mail in delivered] == ["0", "1"]
    finally:
        await member.shutdown()
        await member._event_bus.shutdown()


async def test_while_requests_two_sessions(tmp_path: Path) -> None:
    member = _member(
        tmp_path, _spec(f"(while [i 0] (< i 2) (run-loop (quote {RECIPE})) (recur (inc i)))")
    )
    member._run_session_effect = AsyncMock()
    await member.initialize()
    try:
        await member._on_event(_event(_mail()))
        await _drain(member)
        assert member._run_session_effect.await_count == 2
    finally:
        await member.shutdown()


async def test_when_and_bound_effect_values_do_not_repeat_requests(tmp_path: Path) -> None:
    member = _member(
        tmp_path,
        _spec(
            '(when true (let [request (emit "first" ["sink@tlon.internal"])] [request request]) (emit "second" ["sink@tlon.internal"]))'
        ),
    )
    requests = member._evaluate_rule(member.spec.rules[0], member._env(None))
    assert [request.data["content"] for request in requests] == ["first", "second"]


async def test_collector_preserves_host_start_all_once(tmp_path: Path) -> None:
    host = MagicMock()
    host.start_all_candidates.return_value = ["worker-a", "worker-b"]
    member = _member(
        tmp_path,
        _spec('(when true (start-all {:reason "boot"}) (emit "done" ["sink@tlon.internal"]))'),
    )
    member._builtins = make_member_builtins(host)
    requests = member._evaluate_rule(member.spec.rules[0], member._env(None))
    assert [request.kind for request in requests] == ["start", "start", "emit"]
    assert [request.data["agent_id"] for request in requests[:2]] == ["worker-a", "worker-b"]
    host.start_all_candidates.assert_called_once()


async def test_failed_evaluation_does_not_submit_collected_effects(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec('(emit "must not send" ["sink@tlon.internal"]) (/ 1 0)'))
    member._execute_action = AsyncMock()
    await member.initialize()
    try:
        await member._on_event(_event(_mail()))
        await _drain(member)
        member._execute_action.assert_not_awaited()
    finally:
        await member.shutdown()


async def test_resumed_second_session_keeps_remaining_effects(tmp_path: Path) -> None:
    from tlon.agents.session import AgentSession
    from tlon.agents.vocabulary import make_member_builtins
    from tlosure.edn import dumps
    from tlosure.eval import eval_form

    member = _member(tmp_path, _spec('(emit "unused" ["sink@tlon.internal"])'))
    session_request = eval_form(loads(f"(run-loop (quote {RECIPE}))"), {}, make_member_builtins())
    forward_request = eval_form(
        loads('(forward {:to ["sink@tlon.internal"]})'), {}, make_member_builtins()
    )
    session = AgentSession(
        "resumed-first",
        "source",
        invocation={
            "rule_name": "main",
            "tail": [
                {"kind": request.kind, "data_edn": dumps(request.data)}
                for request in [session_request, forward_request]
            ],
        },
    )

    async def interrupted(*args: Any) -> None:
        assert args[2] == [forward_request]
        raise asyncio.CancelledError()

    member._run_session_effect = AsyncMock(side_effect=interrupted)
    with pytest.raises(asyncio.CancelledError):
        await member._resume_invocation(session)
    assert member._run_session_effect.await_count == 1
    # A second crash uses exactly the tail handed to the second session.
    tail = member._run_session_effect.await_args.args[2]
    second = AgentSession(
        "resumed-second",
        "source",
        invocation={
            "rule_name": "main",
            "tail": [{"kind": request.kind, "data_edn": dumps(request.data)} for request in tail],
        },
    )
    member._execute_action = AsyncMock()
    await member._resume_invocation(second)
    member._execute_action.assert_awaited_once()
    assert member._execute_action.await_args.args[0].kind == "forward"


async def test_empty_env_capability_closure_keeps_nested_effects_once(tmp_path: Path) -> None:
    from tlosure.builtins import Builtin, Closure

    member = _member(
        tmp_path, _spec("(while [i 0] (< i 2) (publish-pair (str i)) (recur (inc i)))")
    )
    closure = Closure(
        params=["text"],
        rest=None,
        body=[
            loads('(emit text ["sink@tlon.internal"])'),
            loads('(emit (str text "!") ["sink@tlon.internal"])'),
        ],
        env={},
        name="publish-pair",
    )
    member._builtins["publish-pair"] = Builtin.from_closure(closure, pure=False)
    requests = member._evaluate_rule(member.spec.rules[0], member._env(None))
    assert [request.data["content"] for request in requests] == ["0", "0!", "1", "1!"]
    assert closure.env == {}
