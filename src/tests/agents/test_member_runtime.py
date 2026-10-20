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
    member = _member(
        tmp_path, _spec('(map n (mail-send {:to ["sink@tlon.internal"] :content (str n)}) [1 2 3])')
    )
    member._post_office.send = AsyncMock(wraps=member._post_office.send)
    await member.initialize()
    try:
        await member._on_event(_event(_mail()))
        await _drain(member)
        assert [call.kwargs["content"] for call in member._post_office.send.await_args_list] == [
            "1",
            "2",
            "3",
        ]
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
        tmp_path,
        _spec(
            f'(run-loop (quote {RECIPE})) (mail-forward {{:mail_id (mail-field msg :id) :to ["sink@tlon.internal"]}})'
        ),
    )
    order: list[str] = []
    release = asyncio.Event()

    async def run(*args: Any, **kwargs: Any) -> None:
        order.append("session")
        await release.wait()

    member._run_session_effect = run
    send = member._post_office.send

    async def forward(**kwargs: Any) -> Any:
        order.append("forward")
        return await send(**kwargs)

    member._post_office.send = AsyncMock(side_effect=forward)
    await member.initialize()
    mail = _mail()
    await member._mailbox.append_inbox(mail)
    try:
        await member._on_event(_event(mail))

        async def wait_started() -> None:
            while not order:
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_started(), 2)
        assert order == ["session"]
        assert member.get_state() == "active"
        release.set()
        await _drain(member)
        assert order == ["session", "forward"]
        assert member._post_office.send.await_args.kwargs["forward_of"] == mail.id
    finally:
        release.set()
        await member.shutdown()


async def test_scan_and_delivery_deduplicate_and_preserve_unread(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec('(mail-send {:to ["sink@tlon.internal"] :content "hello"})'))
    member._post_office.send = AsyncMock(wraps=member._post_office.send)
    mail = _mail()
    await member._mailbox.append_inbox(mail)
    await member.initialize()
    try:
        await member._on_event(_event(mail))
        await _drain(member)
        await member._scan_unread_mail()
        await _drain(member)
        member._post_office.send.assert_awaited_once()
        assert [item.id for item in await member._mailbox.unread()] == [mail.id]
    finally:
        await member.shutdown()
    restored = _member(tmp_path, member.spec)
    restored._post_office.send = AsyncMock(wraps=restored._post_office.send)
    await restored.initialize()
    try:
        await _drain(restored)
        restored._post_office.send.assert_not_awaited()
        assert len(await restored._mailbox.unread()) == 1
    finally:
        await restored.shutdown()


async def test_mark_read_is_explicit_effect(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec("(mail-mark-read {:mail_ids [(mail-field msg :id)]})"))
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
    member = _member(
        tmp_path, _spec('(mail-send {:to ["sink@tlon.internal"] :content "x"})'), registry
    )
    member._post_office.send = AsyncMock(wraps=member._post_office.send)
    await member.initialize()
    try:
        await member._on_event(_event(_mail()))
        await _drain(member)
        assert member._post_office.send.await_count == int(allowed)
    finally:
        await member.shutdown()


async def test_distinct_deliveries_execute_serially(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec('(mail-send {:to ["sink@tlon.internal"] :content "x"})'))
    active = 0
    peak = 0

    send = member._post_office.send

    async def execute(*args: Any, **kwargs: Any) -> Any:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return await send(**kwargs)

    member._post_office.send = execute
    await member.initialize()
    try:
        await asyncio.gather(*(member._on_event(_event(_mail())) for _ in range(5)))
        await _drain(member)
        assert peak == 1
    finally:
        await member.shutdown()


async def test_shared_rule_fuel_and_definitions(tmp_path: Path) -> None:
    member = _member(
        tmp_path,
        _spec('(defn f [x] (mail-send {:to ["sink@tlon.internal"] :content x})) (f "done")'),
    )
    member._post_office.send = AsyncMock(wraps=member._post_office.send)
    assert await member._run_rule_body(member.spec.rules[0], _event(_mail()))
    member._post_office.send.assert_awaited_once()
    assert member._post_office.send.await_args.kwargs["content"] == "done"


async def test_inflight_scan_cannot_duplicate_and_shutdown_cancels_work(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec('(mail-send {:to ["sink@tlon.internal"] :content "x"})'))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def execute(*args: Any, **kwargs: Any) -> None:
        entered.set()
        await release.wait()

    member._post_office.send = AsyncMock(side_effect=execute)
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
        tmp_path,
        _spec(
            '(map x (map y (mail-send {:to ["sink@tlon.internal"] :content (str x y)}) [3 4]) [1 2])'
        ),
    )
    member._post_office.send = AsyncMock(wraps=member._post_office.send)
    assert await member._run_rule_body(member.spec.rules[0], _event(_mail()))
    assert [call.kwargs["content"] for call in member._post_office.send.await_args_list] == [
        "13",
        "14",
        "23",
        "24",
    ]


async def test_real_forward_preserves_mail_reference_and_read_state(tmp_path: Path) -> None:
    member = _member(
        tmp_path, _spec('(mail-forward {:mail_id (mail-field msg :id) :to ["sink@tlon.internal"]})')
    )
    mail = _mail()
    await member._mailbox.append_inbox(mail)
    await member.initialize()
    try:
        await _drain(member)
        delivered = await Mailbox(tmp_path / "shared/mail.sqlite3", "sink").inbox()
        assert len(delivered) == 1
        assert delivered[0].forward_of == mail.id
        assert delivered[0].content.endswith(mail.content)
        assert [item.id for item in await member._mailbox.unread()] == [mail.id]
    finally:
        await member.shutdown()


async def test_boot_action_and_reply_keep_common_executor(tmp_path: Path) -> None:
    member = _member(
        tmp_path, _spec('(mail-send {:to ["sink@tlon.internal"] :content "boot"})', "boot")
    )
    member._post_office.send = AsyncMock(wraps=member._post_office.send)
    executed = asyncio.Event()
    send = member._post_office.send

    async def boot_send(**kwargs: Any) -> Any:
        result = await send(**kwargs)
        executed.set()
        return result

    member._post_office.send = AsyncMock(side_effect=boot_send)
    await member.initialize()
    try:
        await asyncio.wait_for(executed.wait(), 2)
        member._post_office.send.assert_awaited_once()
        assert member._post_office.send.await_args.kwargs["content"] == "boot"
    finally:
        await member.shutdown()


async def test_own_mail_is_not_processed(tmp_path: Path) -> None:
    member = _member(tmp_path, _spec('(mail-send {:to ["sink@tlon.internal"] :content "x"})'))
    member._post_office.send = AsyncMock(wraps=member._post_office.send)
    mail = Mail.create(from_address=member.address, to=[member.address], content="self")
    await member._mailbox.append_inbox(mail)
    await member.initialize()
    try:
        await _drain(member)
        member._post_office.send.assert_not_awaited()
        assert await member._mailbox.unread() == []
    finally:
        await member.shutdown()


async def test_rule_gate_has_no_implicit_read_effect(tmp_path: Path) -> None:
    spec = MemberSpec.from_edn(
        loads(
            '{:version 1 :rules [{:name "reject" :on :mail-delivered :when (not true) :actions [(mail-send {:to ["sink@tlon.internal"] :content "x"})]}]}'
        )
    )
    member = _member(tmp_path, spec)
    member._post_office.send = AsyncMock(wraps=member._post_office.send)
    mail = _mail()
    await member._mailbox.append_inbox(mail)
    await member.initialize()
    try:
        await _drain(member)
        member._post_office.send.assert_not_awaited()
        assert len(await member._mailbox.unread()) == 1
        await member._scan_unread_mail()
        assert member._queue.qsize() == 0
    finally:
        await member.shutdown()


async def test_rule_fuel_is_shared_by_gate_and_actions(tmp_path: Path) -> None:
    spec = MemberSpec.from_edn(
        loads(
            '{:version 1 :rules [{:name "budget" :on :mail-delivered :fuel 30 :actions [(while [n 0] (< n 1000) (recur (inc n)))]}]}'
        )
    )
    member = _member(tmp_path, spec)
    member._post_office.send = AsyncMock(wraps=member._post_office.send)
    event = _event(_mail())
    assert await member._run_rule_body(spec.rules[0], event)
    member._post_office.send.assert_not_awaited()
    assert member._program_store.pending() == []
    record = member._program_store.ensure(
        *member._program_record_data(spec.rules[0], event, None, None)
    )
    assert record.completed is False
    assert record.error is not None and "fuel exhausted" in record.error


async def test_real_session_then_forward_uses_same_trigger(tmp_path: Path) -> None:
    from tlon.agents.member_runtime import MemberCapabilities
    from tlon.agents.tool_executor import ToolExecutor
    from tlon.base.config import AgentConfig, LLMConfig
    from tlon.base.llm_client import LLMResponse, ToolCall
    from tlon.memory.models import MemoryConfig
    from tlon.tools.agents.scheduling import FinishTurnTool

    shell = _member(
        tmp_path,
        _spec(
            f'(run-loop (quote {RECIPE})) (mail-forward {{:mail_id (mail-field msg :id) :to ["sink@tlon.internal"]}})'
        ),
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
        assert delivered[0].content.endswith(mail.content)
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
        _spec(
            '(while [i 0] (< i 2) (mail-send {:to ["sink@tlon.internal"] :content (str i)}) (recur (inc i)))'
        ),
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
            '(when true (let [request (mail-send {:to ["sink@tlon.internal"] :content "first"})] [request request]) (mail-send {:to ["sink@tlon.internal"] :content "second"}))'
        ),
    )
    member._post_office.send = AsyncMock(wraps=member._post_office.send)
    assert await member._run_rule_body(member.spec.rules[0], _event(_mail()))
    assert [call.kwargs["content"] for call in member._post_office.send.await_args_list] == [
        "first",
        "second",
    ]


async def test_host_start_all_preserves_order_and_executes_once(tmp_path: Path) -> None:
    host = MagicMock()
    host.start_all_candidates.return_value = ["worker-a", "worker-b"]
    member = _member(
        tmp_path,
        _spec(
            '(when true (start-all {:reason "boot"}) (mail-send {:to ["sink@tlon.internal"] :content "done"}))'
        ),
    )
    member._builtins = {**make_member_builtins(host), **member._operations.builtins()}
    member._execute_action = AsyncMock()
    member._post_office.send = AsyncMock(wraps=member._post_office.send)
    assert await member._run_rule_body(member.spec.rules[0], _event(_mail()))
    requests = [call.args[0] for call in member._execute_action.await_args_list]
    assert [request.kind for request in requests] == ["start", "start"]
    assert [request.data["agent_id"] for request in requests] == ["worker-a", "worker-b"]
    assert member._post_office.send.await_args.kwargs["content"] == "done"
    host.start_all_candidates.assert_called_once()


async def test_later_evaluation_failure_keeps_committed_effects_without_repeating(
    tmp_path: Path,
) -> None:
    member = _member(
        tmp_path,
        _spec(
            '(mail-send {:to ["sink@tlon.internal"] :content "committed before failure"}) (/ 1 0)',
            extra='{:name "next" :on :agent-idle :actions [(state-put {:key "next" :value true})]}',
        ),
    )
    member._post_office.send = AsyncMock(wraps=member._post_office.send)
    event = _event(_mail())
    await member.initialize()
    try:
        await member._on_event(event)
        await _drain(member)
        member._post_office.send.assert_awaited_once()
        assert member._program_store.pending() == []
        record = member._program_store.ensure(
            *member._program_record_data(
                member.spec.rules[0],
                event,
                None,
                None,
            )
        )
        assert record.completed is False
        assert record.error is not None and "division by zero" in record.error
        assert await member._run_rule_body(member.spec.rules[0], event)
        member._post_office.send.assert_awaited_once()
        await member._on_event(
            Event(
                type=EventType.AGENT_IDLE,
                payload={"agent_id": "member"},
                source="test",
            )
        )
        await _drain(member)
        assert member._program_store.state_get("next") is True
    finally:
        await member.shutdown()


async def test_resumed_second_session_keeps_remaining_effects(tmp_path: Path) -> None:
    from tlon.agents.session import AgentSession

    member = _member(
        tmp_path,
        _spec(
            f"(run-loop (quote {RECIPE})) (run-loop (quote {RECIPE})) "
            '(mail-forward {:mail_id (mail-field msg :id) :to ["sink@tlon.internal"]})'
        ),
    )
    event = _event(_mail())
    await member._mailbox.append_inbox(event.payload["mail"])
    rule = member.spec.rules[0]
    interrupted: list[dict[str, Any]] = []

    async def crash(*args: Any, **kwargs: Any) -> None:
        interrupted.append(kwargs["program_effect"])
        raise asyncio.CancelledError()

    member._run_session_effect = AsyncMock(side_effect=crash)
    member._post_office.send = AsyncMock(wraps=member._post_office.send)
    with pytest.raises(asyncio.CancelledError):
        await member._run_rule_body(rule, event)
    first = interrupted[0]
    await member._resume_invocation(
        AgentSession(
            "session-first",
            "source",
            invocation={"program_effect": first},
        )
    )
    with pytest.raises(asyncio.CancelledError):
        await member._run_rule_body(rule, event)
    second = interrupted[1]
    assert first["run_id"] == second["run_id"]
    assert first["index"] == 0 and second["index"] == 1
    assert member._run_session_effect.await_count == 2
    await member._resume_invocation(
        AgentSession(
            "session-second",
            "source",
            invocation={"program_effect": second},
        )
    )
    assert await member._run_rule_body(rule, event)
    assert member._run_session_effect.await_count == 2
    member._post_office.send.assert_awaited_once()
    assert member._post_office.send.await_args.kwargs["forward_of"] == event.payload["mail"].id
    assert member._program_store.pending() == []


async def test_empty_env_capability_closure_keeps_nested_effects_once(tmp_path: Path) -> None:
    from tlosure.builtins import Builtin, Closure

    member = _member(
        tmp_path, _spec("(while [i 0] (< i 2) (publish-pair (str i)) (recur (inc i)))")
    )
    closure = Closure(
        params=["text"],
        rest=None,
        body=[
            loads('(mail-send {:to ["sink@tlon.internal"] :content text})'),
            loads('(mail-send {:to ["sink@tlon.internal"] :content (str text "!")})'),
        ],
        env={},
        name="publish-pair",
    )
    member._builtins["publish-pair"] = Builtin.from_closure(closure, pure=False)
    member._post_office.send = AsyncMock(wraps=member._post_office.send)
    assert await member._run_rule_body(member.spec.rules[0], _event(_mail()))
    assert [call.kwargs["content"] for call in member._post_office.send.await_args_list] == [
        "0",
        "0!",
        "1",
        "1!",
    ]
    assert closure.env == {}


async def test_recovery_executes_frozen_program_after_behavior_update(tmp_path: Path) -> None:
    original = _member(
        tmp_path,
        _spec(
            '(mail-send {:to [(mail-field msg :from)] :content "before" :in_reply_to (mail-field msg :id)}) (mail-send {:to [(mail-field msg :from)] :content "after" :in_reply_to (mail-field msg :id)})'
        ),
    )
    seen: list[str] = []

    send = original._post_office.send

    async def interrupt(**kwargs: Any) -> Any:
        if kwargs["content"] == "after":
            raise asyncio.CancelledError()
        seen.append(kwargs["content"])
        return await send(**kwargs)

    original._post_office.send = AsyncMock(side_effect=interrupt)
    event = _event(_mail())
    await original._mailbox.append_inbox(event.payload["mail"])
    with pytest.raises(asyncio.CancelledError):
        await original._run_rule_body(original.spec.rules[0], event)
    assert seen == ["before"]
    restored = _member(
        tmp_path,
        _spec(
            '(mail-send {:to [(mail-field msg :from)] :content "new behavior" :in_reply_to (mail-field msg :id)})'
        ),
    )
    restored._post_office.send = AsyncMock(wraps=restored._post_office.send)
    await restored._restore_programs()
    restored._post_office.send.assert_awaited_once()
    assert restored._post_office.send.await_args.kwargs["content"] == "after"
    assert restored._program_store.pending() == []


async def test_unknown_mail_submission_keeps_program_pending(tmp_path: Path) -> None:
    member = _member(
        tmp_path,
        _spec('(mail-send {:to ["sink@tlon.internal"] :content "edition" :request_id "p:a"})'),
    )
    member._post_office.send = AsyncMock(side_effect=OSError("submission unknown"))
    from tlon.operations.schema import UnknownOutcomeError

    with pytest.raises(UnknownOutcomeError, match="submission unknown"):
        await member._run_rule_body(member.spec.rules[0], _event(_mail()))
    pending = member._program_store.pending()
    assert len(pending) == 1
    assert pending[0].completed is False and pending[0].error is None
    member._post_office.send.assert_awaited_once()


async def test_pending_failure_blocks_new_state_updates_until_recovery(tmp_path: Path) -> None:
    member = _member(
        tmp_path,
        _spec(
            '(mail-send {:to [(mail-field msg :from)] :content "blocked" :in_reply_to (mail-field msg :id)})',
            extra='{:name "new" :on :agent-idle :actions [(state-put {:key "new" :value true})]}',
        ),
    )
    blocked = True
    delivered: list[str] = []
    retry_entered = asyncio.Event()
    attempts = 0

    send = member._post_office.send

    async def execute(**kwargs: Any) -> Any:
        nonlocal attempts
        attempts += 1
        if blocked:
            if attempts >= 2:
                retry_entered.set()
            raise OSError("previous effect still unavailable")
        delivered.append(kwargs["content"])
        return await send(**kwargs)

    member._post_office.send = AsyncMock(side_effect=execute)
    idle = Event(type=EventType.AGENT_IDLE, payload={"agent_id": "member"}, source="test")
    await member.initialize()
    try:
        mail = _mail()
        await member._mailbox.append_inbox(mail)
        await member._on_event(_event(mail))
        await _drain(member)
        assert len(member._program_store.pending()) == 1
        await member._on_event(idle)
        await asyncio.wait_for(retry_entered.wait(), 2)
        assert member._program_store.state_get("new") is None
        assert delivered == []
        assert member._queue.qsize() == 1
        blocked = False
        await _drain(member)
        assert delivered == ["blocked"]
        assert member._program_store.state_get("new") is True
    finally:
        await member.shutdown()


async def test_boot_io_failure_recovers_without_stopping_actor(tmp_path: Path) -> None:
    member = _member(
        tmp_path,
        _spec(
            '(state-put {:key "boot" :value true})',
            "boot",
            extra='{:name "later" :on :agent-idle :actions [(state-put {:key "later" :value true})]}',
        ),
    )
    first_attempt = asyncio.Event()
    recovered = asyncio.Event()
    execute = member._program_store.atomic_state_put_record
    attempts = 0
    loop = asyncio.get_running_loop()

    def transient(run_id: str, index: int, name: str, args_edn: str, key: str, value: Any) -> Any:
        nonlocal attempts
        if key == "boot":
            attempts += 1
            if attempts == 1:
                loop.call_soon_threadsafe(first_attempt.set)
                raise OSError("temporary boot storage failure")
            result = execute(run_id, index, name, args_edn, key, value)
            loop.call_soon_threadsafe(recovered.set)
            return result
        return execute(run_id, index, name, args_edn, key, value)

    member._program_store.atomic_state_put_record = transient
    await member.initialize()
    try:
        await asyncio.wait_for(first_attempt.wait(), 2)
        await member._on_event(
            Event(
                type=EventType.AGENT_IDLE,
                payload={"agent_id": "member"},
                source="test",
            )
        )
        await asyncio.wait_for(recovered.wait(), 3)
        await _drain(member)
        assert attempts == 2
        assert member._program_store.state_get("boot") is True
        assert member._program_store.state_get("later") is True
        assert member._program_store.pending() == []
        assert member._program_task is not None and not member._program_task.done()
    finally:
        await member.shutdown()


@pytest.mark.parametrize("allowed", [False, True])
async def test_mail_only_rule_updates_activity_only_when_invoked(
    tmp_path: Path, allowed: bool
) -> None:
    from unittest.mock import patch

    member = _member(
        tmp_path,
        MemberSpec.from_edn(
            loads(
                '{:version 1 :rules [{:name "mail" :on :mail-delivered :when '
                + ("(= 1 1)" if allowed else "(= 1 2)")
                + ' :actions [(mail-send {:to ["sink@tlon.internal"] :content "activity"})]}]}'
            )
        ),
    )
    member._initialized_at = 100.0
    with patch("tlon.agents.member_runtime.time.monotonic", return_value=200.0):
        await member._run_rule_body(member.spec.rules[0], _event(_mail()))
        assert member._last_action_at == (200.0 if allowed else None)
        assert member.idle_seconds == (0.0 if allowed else 100.0)


async def test_mail_program_continues_trigger_trace(tmp_path: Path) -> None:
    from tlon.base.tracing.trace_context import get_current_trace

    member = _member(tmp_path, _spec('(mail-send {:to ["sink@tlon.internal"] :content "traced"})'))
    send = member._post_office.send
    traces: list[str] = []

    async def traced_send(**kwargs: Any) -> Any:
        context = get_current_trace()
        assert context is not None
        traces.append(context.trace_id)
        return await send(**kwargs)

    member._post_office.send = traced_send
    event = _event(_mail())
    event.trace_id = "12345678901234567890123456789012"
    event.span_id = "1234567890123456"
    await member._run_rule_body(member.spec.rules[0], event)
    assert traces == [event.trace_id]
