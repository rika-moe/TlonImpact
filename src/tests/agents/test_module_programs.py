"""Module entry programs compose and replay without the legacy session engine."""

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.mail_helpers import make_office
from tlon.agents.member_runtime import MemberRuntime
from tlon.agents.member_spec import MemberSpecValidationError
from tlon.agents.program_package import read_module_sources, validate_program_sources
from tlon.base.events.event import Event, EventType
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlosure.edn import loads

BEHAVIOR = "{:version 2 :handlers {:mail-delivered work.reply/handle} :fuel 1000}"
MODULES = {
    "work/reply.tlon": """
    (ns work.reply (:require [work.compose :as compose]) (:export handle))
    (defn handle [event]
      (let [content (compose/text (get event :mail))]
        (compose/local-scheduling)
        (mail-send {:to [(get event :sender)] :content content})
        {:result {:content content :type (get event :type)}}))
    """,
    "work/compose.tlon": """
    (ns work.compose (:export text local-scheduling))
    (defn text [mail] (str "reply:" (get mail :content)))
    (defn local-scheduling [] {:result "inner" :next {:kind "exit"}})
    """,
}


def _member(
    home: Path, modules: dict[str, str] | None = None, registry: Any = None
) -> MemberRuntime:
    from tlon.agents.vocabulary import make_member_builtins

    bus = EventBus()
    return MemberRuntime(
        member_id="member",
        name="Member",
        event_bus=bus,
        spec=validate_program_sources(BEHAVIOR, MODULES if modules is None else modules),
        builtins=make_member_builtins(),
        mailbox=Mailbox(home / "shared/mail.sqlite3", "member"),
        post_office=make_office(bus, home, ["member", "sender"]),
        registry=registry,
        tlon_paths=TlonPaths(home=home),
    )


def _event() -> Event:
    mail = Mail.create(
        from_address="sender@tlon.internal", to=["member@tlon.internal"], content="hello"
    )
    return Event(
        type=EventType.MAIL_DELIVERED, payload={"mail": mail, "recipient": "member"}, source="test"
    )


async def test_two_module_mail_entry_returns_result_without_nested_scheduling(
    tmp_path: Path,
) -> None:
    registry = MagicMock()
    registry.prepare_scheduling_request = AsyncMock()
    registry.apply_program_scheduling = AsyncMock()
    registry.publish_scheduling_state = AsyncMock()
    member = _member(tmp_path, registry=registry)
    event = _event()
    member._run_session_effect = AsyncMock(side_effect=AssertionError("no stage fallback"))
    await member.initialize()
    try:
        await member._on_event(event)
        await asyncio.wait_for(member._queue.join(), 2)
        record = member._program_store.get_run(f"{event.payload['mail'].id}:handler:mail-delivered")
        assert record is not None and record.completed and record.error is None
        assert loads(record.result_edn) == {"content": "reply:hello", "type": "mail.delivered"}
        assert record.scheduling_edn is None
        inbox = Mailbox(tmp_path / "shared/mail.sqlite3", "sender")
        assert [mail.content for mail in await inbox.unread()] == ["reply:hello"]
        registry.prepare_scheduling_request.assert_not_awaited()
        registry.apply_program_scheduling.assert_not_awaited()
        member._run_session_effect.assert_not_awaited()
        assert member._scheduler is None
    finally:
        await member.shutdown()


async def test_restart_replays_frozen_modules_after_committed_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    member = _member(tmp_path)
    event = _event()
    await member.initialize()
    original = member._program_store.complete_entry

    def crash(*args: Any, **kwargs: Any) -> Any:
        raise asyncio.CancelledError()

    monkeypatch.setattr(member._program_store, "complete_entry", crash)
    try:
        with pytest.raises(asyncio.CancelledError):
            await member._run_rule_body(member.spec.rules[0], event, run_id="frozen-run")
        saved = member._program_store.get_run("frozen-run")
        assert saved is not None and not saved.completed
    finally:
        monkeypatch.setattr(member._program_store, "complete_entry", original)
        await member.shutdown()
    changed = dict(MODULES)
    changed["work/compose.tlon"] = changed["work/compose.tlon"].replace("reply:", "changed:")
    restored = _member(tmp_path, changed)
    restored._post_office.send = AsyncMock(wraps=restored._post_office.send)
    await restored.initialize()
    try:
        async with asyncio.timeout(2):
            while True:
                record = restored._program_store.get_run("frozen-run")
                if record is not None and record.completed:
                    break
                await asyncio.sleep(0.01)
        assert record is not None and record.completed and record.error is None
        assert loads(record.result_edn)["content"] == "reply:hello"
        restored._post_office.send.assert_not_awaited()
        inbox = Mailbox(tmp_path / "shared/mail.sqlite3", "sender")
        assert [mail.content for mail in await inbox.unread()] == ["reply:hello"]
    finally:
        await restored.shutdown()


@pytest.mark.parametrize(
    "behavior,modules",
    [
        ("{:version 1 :rules []}", MODULES),
        ("{:version 2 :handlers {:mail-delivered work.reply/missing}}", MODULES),
        ("{:version 2 :handlers {:mail-delivered work.reply/handle} :rules []}", MODULES),
        ("{:version 2 :handlers {:mail-delivered work.reply/handle} :fuel 0}", MODULES),
        (
            BEHAVIOR,
            {"work/reply.tlon": "(ns work.reply (:export handle)) (defn handle [x] (run-loop {}))"},
        ),
    ],
)
def test_invalid_module_program_never_falls_back_to_rules(
    behavior: str, modules: dict[str, str]
) -> None:
    with pytest.raises(MemberSpecValidationError):
        validate_program_sources(behavior, modules)


def test_module_source_loader_rejects_non_source_files_and_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "modules"
    root.mkdir()
    (root / "main.txt").write_text("not source")
    with pytest.raises(MemberSpecValidationError, match="unexpected module file"):
        read_module_sources(root)
    (root / "main.txt").unlink()
    outside = tmp_path / "outside.tlon"
    outside.write_text("(ns outside)")
    (root / "main.tlon").symlink_to(outside)
    with pytest.raises(MemberSpecValidationError, match="symlinks"):
        read_module_sources(root)


async def test_entry_scheduling_restarts_from_committed_result(tmp_path: Path) -> None:
    registry = MagicMock()
    registry.prepare_scheduling_request = AsyncMock()
    registry.apply_program_scheduling = AsyncMock(side_effect=asyncio.CancelledError())
    registry.publish_scheduling_state = AsyncMock()
    modules = {
        "work/reply.tlon": """
        (ns work.reply (:export handle))
        (defn handle [event] {:result "done" :next {:kind "exit"}})
        """
    }
    member = _member(tmp_path, modules, registry)
    await member.initialize()
    try:
        with pytest.raises(asyncio.CancelledError):
            await member._run_rule_body(member.spec.rules[0], _event(), run_id="schedule-run")
        record = member._program_store.get_run("schedule-run")
        assert record is not None and record.completed and not record.scheduling_applied
        assert loads(record.result_edn) == "done"
        assert loads(record.scheduling_edn)["action"] == "exit"
    finally:
        await member.shutdown()
    recovered_registry = MagicMock()
    recovered_registry.prepare_scheduling_request = AsyncMock()
    recovered_registry.apply_program_scheduling = AsyncMock()
    recovered_registry.publish_scheduling_state = AsyncMock()
    recovered = _member(tmp_path, registry=recovered_registry)
    await recovered.initialize()
    try:
        async with asyncio.timeout(2):
            while True:
                record = recovered._program_store.get_run("schedule-run")
                if record is not None and record.scheduling_applied:
                    break
                await asyncio.sleep(0.01)
        assert record.completed
        assert recovered_registry.apply_program_scheduling.await_args.args[1] == "schedule-run"
        recovered_registry.apply_program_scheduling.assert_awaited_once()
        await recovered._run_entry_record(record)
        recovered_registry.apply_program_scheduling.assert_awaited_once()
    finally:
        await recovered.shutdown()


@pytest.mark.parametrize(
    "result", ['"invalid"', "{}", '{:next {:kind "exit"}}', '{:result 1 :next {:kind "bogus"}}']
)
async def test_invalid_entry_return_is_failed_without_stage_fallback(
    tmp_path: Path, result: str
) -> None:
    modules = {
        "work/reply.tlon": f"(ns work.reply (:export handle)) (defn handle [event] {result})"
    }
    member = _member(tmp_path, modules)
    member._run_session_effect = AsyncMock(side_effect=AssertionError("no fallback"))
    await member.initialize()
    try:
        await member._run_rule_body(member.spec.rules[0], _event(), run_id="bad-result")
        record = member._program_store.get_run("bad-result")
        assert record is not None and record.error is not None
        assert "entry" in record.error
        assert record.scheduling_edn is None
        member._run_session_effect.assert_not_awaited()
    finally:
        await member.shutdown()


async def test_v2_with_real_services_does_not_start_stage_scheduler(tmp_path: Path) -> None:
    from tlon.agents.member_runtime import MemberServices
    from tlon.agents.tool_executor import ToolExecutor
    from tlon.agents.vocabulary import make_member_builtins
    from tlon.base.config import AgentConfig, LLMConfig
    from tlon.base.execution import MemberWriteGate
    from tlon.memory.models import MemoryConfig

    shell = _member(tmp_path)
    llm = MagicMock()
    llm.config = LLMConfig(model_name="openai/gpt-test")
    llm.chat = AsyncMock(side_effect=AssertionError("P1 requires no model"))
    memory = MagicMock()
    memory.config = MemoryConfig()
    memory.load = AsyncMock()
    memory.maybe_distill_completed_sessions = AsyncMock(return_value=0)
    services = MemberServices(
        write_gate=MemberWriteGate(),
        config=AgentConfig(id="member", name="Member"),
        llm_client=llm,
        memory=memory,
        task_manager=MagicMock(),
        tool_executor=ToolExecutor(),
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
        services=services,
    )
    assert member._scheduler is None
    await member.initialize()
    try:
        await member._on_event(_event())
        await asyncio.wait_for(member._queue.join(), 2)
        assert member._scheduler is None
        llm.chat.assert_not_awaited()
        inbox = Mailbox(tmp_path / "shared/mail.sqlite3", "sender")
        assert [mail.content for mail in await inbox.unread()] == ["reply:hello"]
    finally:
        await member.shutdown()


async def test_missing_frozen_operation_is_quarantined_without_blocking_new_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tlon.agents.vocabulary import make_member_builtins

    original = _member(tmp_path)
    event = _event()
    await original.initialize()
    complete = original._program_store.complete_entry

    def crash(*args: Any, **kwargs: Any) -> Any:
        raise asyncio.CancelledError()

    monkeypatch.setattr(original._program_store, "complete_entry", crash)
    try:
        with pytest.raises(asyncio.CancelledError):
            await original._run_rule_body(original.spec.rules[0], event, run_id="missing-operation")
    finally:
        monkeypatch.setattr(original._program_store, "complete_entry", complete)
        await original.shutdown()
    current_modules = {
        "work/reply.tlon": """
        (ns work.reply (:export handle))
        (defn handle [event]
          (state-put {:key "new-event" :value true})
          {:result "new-work"})
        """
    }
    # The language registry/build is unchanged; the mail service is no longer
    # assembled. Its frozen invocation must not fall back to a legacy action.
    restored = MemberRuntime(
        member_id="member",
        name="Member",
        event_bus=EventBus(),
        spec=validate_program_sources(BEHAVIOR, current_modules),
        builtins=make_member_builtins(),
        tlon_paths=TlonPaths(home=tmp_path),
    )
    await restored.initialize()
    try:
        await restored._on_event(_event())
        await asyncio.wait_for(restored._queue.join(), 2)
        blocked = restored._program_store.get_run("missing-operation")
        assert blocked is not None and blocked.error is not None
        assert blocked.error.startswith("recovery_blocked:")
        assert restored._program_store.pending() == []
        assert restored._program_store.state_get("new-event") is True
    finally:
        await restored.shutdown()


@pytest.mark.parametrize("at", ["2000-01-01T00:00:00+00:00", "2999-01-01T00:00:00", "bad"])
def test_entry_sleep_preserves_future_timezone_constraint(at: str) -> None:
    from tlosure.builtins import EvalError

    with pytest.raises(EvalError, match="future ISO"):
        MemberRuntime._entry_result({"result": None, "next": {"kind": "sleep-until", "at": at}})
