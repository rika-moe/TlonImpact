"""Session journal (ADR 0007) + the single-session scheduler (ADR 0028).

Pins the append/resume roundtrip (including the journaled responsibility
snapshot), the orphan-dispatch self-healing, the "model-visible ⇒
journaled" invariant (derive_context == session.context after a full
driver run), crash recovery via resume_collect, boot recovery through
``find_unfinished``/``_restore_unfinished``, original recipe and invocation
recovery, and the abnormal-termination failure replies — anchored on the
responsibility set's human senders (ADR 0027 as revised by ADR 0028).
"""

import asyncio
import contextlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.recipe_helpers import recipe_for
from tlon.agents.context_builder import BuiltContext
from tlon.agents.context_pressure import ContextPressure
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.member_spec import MemberSpec, validate_member_spec
from tlon.agents.scheduler import SessionScheduler
from tlon.agents.session import AgentSession, SessionState
from tlon.agents.session_journal import INTERRUPTED_ERROR, SessionJournal
from tlon.agents.stages.outcome import StageOutcome
from tlon.agents.trigger import AgentIdentity
from tlon.base.config import AgentConfig
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.tools.base import ToolResult
from tlon.tools.spill import ToolSpill
from tlosure.edn import dumps, loads

SESSION_PROGRAM_EDN = '{:version 1 :rules [{:name "respond" :on :mail-delivered :actions [(run-loop (quote {:context {:sections [(persona) (protocol)]} :stages [(think) (act)] :until (or (iteration-limit-reached?) (error-limit-reached?)) :max-iterations 10 :max-consecutive-errors 3 :max-tokens 1000000 :reflect {:enabled false :interval-minutes 5}}))]}]}'

#: Same session recipe with all three session :replies overrides declared.
REPLIES_EDN = SESSION_PROGRAM_EDN.replace(
    ":reflect {:enabled false :interval-minutes 5}",
    ":reflect {:enabled false :interval-minutes 5}\n"
    '                 :replies {:iteration-limit "想太久了，简单说。"\n'
    '                           :error "出错了。"\n'
    '                           :timeout "超时了。"}',
)

#: The wake trigger text used by driver-level tests (ADR 0028 template).
TRIGGER = "你有 1 封未读邮件"


@pytest.fixture
def tlon(tmp_path: Path) -> TlonPaths:
    return TlonPaths(home=tmp_path)


@pytest.fixture
def journal(tlon: TlonPaths) -> SessionJournal:
    return SessionJournal("agent1", tlon)


def _mail(content: str = "hi", sender: str = "user@tlon.internal") -> Mail:
    return Mail.create(from_address=sender, content=content, to=["agent1@tlon.internal"])


def _tc(tc_id: str, name: str = "someTool", arguments: dict | None = None) -> ToolCall:
    return ToolCall(id=tc_id, function_name=name, arguments=arguments or {})


def _session(sid: str, *mails: Mail) -> AgentSession:
    """A session as the scheduler would open it: wake-template trigger
    text plus the responsibility set (ADR 0028)."""
    return AgentSession(session_id=sid, trigger_content=TRIGGER, responsibility=list(mails))


async def _start(
    journal: SessionJournal, sid: str, responsibility: list[str] | None = None
) -> None:
    await journal.append(
        sid,
        "session/start",
        agent_id="agent1",
        trigger_content=TRIGGER,
        recipe=dumps(
            recipe_for(MemberSpec.from_edn(loads(SESSION_PROGRAM_EDN)).rules[0]).to_edn_data()
        ),
        carrier={"trace_id": "t-1"},
        responsibility=responsibility or [],
        context=[{"role": "user", "content": "hi"}],
    )


# ── Journal: append / resume ────────────────────────────────────────────


class TestJournalReplay:
    async def test_append_resume_roundtrip(self, journal: SessionJournal) -> None:
        sid = "agent1-deadbeef"
        await _start(journal, sid, responsibility=["m1", "m2"])
        await journal.append(
            sid, "context/append", messages=[{"role": "assistant", "content": "yo"}]
        )
        await journal.append(sid, "scratch/update", updates={"plan": "p1"})
        await journal.append(sid, "iteration", value=2)
        await journal.append(sid, "errors", value=1)
        response = LLMResponse(
            content="yo", tool_calls=[_tc("c1", "do", {"a": 1})], reasoning_content="r"
        )
        await journal.append(sid, "llm/response", response=response.to_dict())
        result = ToolResult(success=True, data={"out": 1}, message="ok")
        await journal.append(sid, "tool/dispatch", tool_calls=[_tc("c1", "do").to_dict()])
        await journal.append(
            sid, "tool/result", tool_call=_tc("c1", "do").to_dict(), result=result.to_dict()
        )
        await journal.append(sid, "tool/collect", tool_call_ids=["c1"])
        await journal.append(sid, "state", value="active")

        replay = await journal.resume(sid)

        assert replay is not None and not replay.completed and replay.end_reason is None
        # ADR 0028: the responsibility snapshot survives the roundtrip.
        assert replay.responsibility == ["m1", "m2"]
        assert replay.context == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
        ]
        assert replay.loop_scratch == {"plan": "p1"}
        assert replay.iteration == 2
        assert replay.error_count == 1
        assert replay.carrier == {"trace_id": "t-1"}
        assert replay.last_llm_response is not None
        assert replay.last_llm_response.content == "yo"
        assert replay.last_llm_response.reasoning_content == "r"
        assert replay.last_llm_response.tool_calls is not None
        assert replay.last_llm_response.tool_calls[0].function_name == "do"
        assert replay.last_llm_response.tool_calls[0].arguments == {"a": 1}
        assert len(replay.all_tool_results) == 1
        entry = replay.all_tool_results[0]
        assert entry["tool_call"].id == "c1"
        assert entry["result"].success is True
        assert entry["result"].data == {"out": 1}
        assert entry["result"].message == "ok"
        assert replay.state == SessionState.ACTIVE
        assert replay.pending_collect == []

    async def test_resume_without_log_returns_none(self, journal: SessionJournal) -> None:
        assert await journal.resume("agent1-nope") is None

    async def test_legacy_log_without_responsibility_falls_back_to_empty(
        self, journal: SessionJournal
    ) -> None:
        """Logs predating the responsibility field (in-flight sessions from
        before ADR 0028) replay with the empty set — no compat layer."""
        sid = "agent1-legacy"
        await journal.append(
            sid,
            "session/start",
            agent_id="agent1",
            mail_id="m1",
            trigger_content="hi",
            carrier=None,
            context=[{"role": "user", "content": "hi"}],
        )

        replay = await journal.resume(sid)

        assert replay is not None
        assert replay.responsibility == []

    async def test_tokens_event_replays_absolute_total(self, journal: SessionJournal) -> None:
        """ADR 0023: ``tokens`` events carry the post-accumulation absolute
        total (same pattern as iteration/errors), so replay restores the
        session's token budget consumption."""
        sid = "agent1-tokens"
        await _start(journal, sid)
        await journal.append(sid, "tokens", value=150)
        await journal.append(sid, "tokens", value=850)

        replay = await journal.resume(sid)

        assert replay is not None
        assert replay.token_usage == 850
        scheduler = _make_scheduler(journal)
        session = await scheduler._restore_session(replay)
        assert session.token_usage == 850

    async def test_completed_log_marks_session_done(self, journal: SessionJournal) -> None:
        sid = "agent1-done"
        await _start(journal, sid)
        await journal.append(sid, "session/end", reason="complete")

        replay = await journal.resume(sid)

        assert replay is not None
        assert replay.completed is True
        assert replay.end_reason == "complete"
        assert await journal.end_reason(sid) == "complete"

    async def test_orphan_dispatch_heals_with_interrupted_result(
        self, journal: SessionJournal
    ) -> None:
        sid = "agent1-orphan"
        await _start(journal, sid)
        done, lost = _tc("c-done"), _tc("c-lost", "slowTool")
        await journal.append(sid, "tool/dispatch", tool_calls=[done.to_dict(), lost.to_dict()])
        await journal.append(
            sid,
            "tool/result",
            tool_call=done.to_dict(),
            result=ToolResult(success=True, data={"ok": 1}).to_dict(),
        )
        await journal.append(sid, "state", value="waiting")

        replay = await journal.resume(sid)

        assert replay is not None and replay.state == SessionState.WAITING
        by_id = {tc.id: result for tc, result in replay.pending_collect}
        assert set(by_id) == {"c-done", "c-lost"}
        assert by_id["c-done"].success is True
        assert by_id["c-done"].data == {"ok": 1}
        assert by_id["c-lost"].success is False
        assert by_id["c-lost"].error == INTERRUPTED_ERROR

        # Self-healing: the synthetic result is now part of the log.
        healed = await journal.resume(sid)
        assert healed is not None
        assert {tc.id for tc, _ in healed.pending_collect} == {"c-done", "c-lost"}
        events = await journal.read_events(healed.session_id)
        synthetic = [
            e for e in events if e.get("type") == "tool/result" and e["tool_call"]["id"] == "c-lost"
        ]
        assert len(synthetic) == 1
        assert synthetic[0]["result"]["error"] == INTERRUPTED_ERROR

    async def test_derive_context_rebuilds_model_view(self, journal: SessionJournal) -> None:
        sid = "agent1-ctx"
        await _start(journal, sid)
        await journal.append(
            sid, "context/append", messages=[{"role": "assistant", "content": "a"}]
        )
        await journal.append(sid, "context/append", messages=[{"role": "tool", "content": "b"}])

        assert await journal.derive_context(sid) == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "a"},
            {"role": "tool", "content": "b"},
        ]

    async def test_explicit_anchor_len_wins_on_replay(self, journal: SessionJournal) -> None:
        """ADR 0017: session/start carries the anchor length explicitly —
        the trailing knowledge message is outside the anchor."""
        sid = "agent1-anchor-explicit"
        await journal.append(
            sid,
            "session/start",
            agent_id="agent1",
            trigger_content=TRIGGER,
            carrier=None,
            responsibility=["m1"],
            context=[
                {"role": "user", "content": TRIGGER},
                {"role": "user", "content": "[已知信息]\n\nclaim"},
            ],
            anchor_len=1,
        )

        replay = await journal.resume(sid)

        assert replay is not None
        assert replay.anchor_len == 1
        assert len(replay.context) == 2

    async def test_missing_anchor_len_falls_back_to_context_length(
        self, journal: SessionJournal
    ) -> None:
        """Logs written before the field existed (in-flight sessions):
        the whole initial context is the anchor, the old behavior."""
        sid = "agent1-anchor-legacy"
        await _start(journal, sid)  # no anchor_len field

        replay = await journal.resume(sid)

        assert replay is not None
        assert replay.anchor_len == 1 == len(replay.context)


class TestSqliteJournal:
    async def test_restart_queries_and_replays_only_the_requested_session(
        self, tlon: TlonPaths, journal: SessionJournal
    ) -> None:
        await _start(journal, "agent1-done")
        await journal.append(
            "agent1-done", "context/append", messages=[{"role": "assistant", "content": "ok"}]
        )
        await journal.append("agent1-done", "session/end", reason="complete")
        await _start(journal, "agent1-open")

        restarted = SessionJournal("agent1", tlon)

        done_events = await restarted.read_events("agent1-done")
        assert [event["seq"] for event in done_events] == [0, 1, 2]
        assert {event["session_id"] for event in done_events} == {"agent1-done"}
        assert [event["type"] for event in await restarted.read_events("agent1-open")] == [
            "session/start"
        ]
        assert await restarted.list_completed() == ["agent1-done"]
        assert await restarted.latest_completed() == "agent1-done"
        assert await restarted.find_unfinished() == ["agent1-open"]
        assert await restarted.end_reason("agent1-done") == "complete"
        replay = await restarted.resume("agent1-done")
        assert replay is not None and replay.completed
        assert replay.context[-1] == {"role": "assistant", "content": "ok"}

    async def test_concurrent_append_allocates_conflict_free_sequences(
        self, tlon: TlonPaths, journal: SessionJournal
    ) -> None:
        sid = "agent1-concurrent"
        await _start(journal, sid)
        writers = [SessionJournal("agent1", tlon) for _ in range(4)]

        await asyncio.gather(
            *(
                writers[index % len(writers)].append(sid, "iteration", value=index)
                for index in range(32)
            )
        )

        events = await journal.read_events(sid)
        assert [event["seq"] for event in events] == list(range(33))
        assert sorted(event["value"] for event in events[1:]) == list(range(32))

    async def test_legacy_jsonl_is_neither_read_nor_written(
        self, tlon: TlonPaths, tmp_path: Path
    ) -> None:
        sid = "agent1-legacy"
        legacy_dir = tmp_path / "agent1" / "sessions"
        legacy_dir.mkdir(parents=True)
        legacy_file = legacy_dir / f"{sid}.jsonl"
        legacy_text = "".join(
            json.dumps(event) + "\n"
            for event in [
                {"seq": 90, "type": "session/start", "session_id": sid, "context": []},
                {"seq": 91, "type": "session/end", "session_id": sid, "reason": "complete"},
            ]
        )
        legacy_file.write_text(legacy_text, encoding="utf-8")
        journal = SessionJournal("agent1", tlon)

        assert await journal.read_events(sid) == []
        assert await journal.resume(sid) is None
        assert await journal.list_completed() == []
        assert await journal.latest_completed() is None
        assert await journal.find_unfinished() == []
        assert not tlon.agent_sessions_database_file("agent1").exists()

        await _start(journal, sid)

        events = await journal.read_events(sid)
        assert len(events) == 1 and events[0]["seq"] == 0
        assert legacy_file.read_text(encoding="utf-8") == legacy_text
        assert list(legacy_dir.glob("*.jsonl")) == [legacy_file]
        assert tlon.agent_sessions_database_file("agent1").is_file()

    async def test_lifecycle_queries_never_read_event_bodies(self, journal: SessionJournal) -> None:
        await _start(journal, "agent1-open")
        await _start(journal, "agent1-closed")
        await journal.append("agent1-closed", "session/end", reason="complete")
        store_events = MagicMock(side_effect=AssertionError("lifecycle query read event bodies"))
        journal._store.events = store_events  # type: ignore[method-assign]

        assert await journal.list_completed() == ["agent1-closed"]
        assert await journal.count_completed() == 1
        assert await journal.latest_completed() == "agent1-closed"
        assert await journal.find_unfinished() == ["agent1-open"]
        assert await journal.end_reason("agent1-closed") == "complete"
        store_events.assert_not_called()


class TestFindUnfinished:
    """Boot recovery index (ADR 0007 + ADR 0028): started metadata without
    end metadata is unfinished; completed and start-less sessions are not."""

    async def test_unfinished_and_completed_partition(self, journal: SessionJournal) -> None:
        await _start(journal, "agent1-open")
        await _start(journal, "agent1-closed")
        await journal.append("agent1-closed", "session/end", reason="complete")
        # Stray events without session/start are not recoverable sessions.
        await journal.append("agent1-stray", "iteration", value=1)

        assert await journal.find_unfinished() == ["agent1-open"]

    async def test_empty_database(self, journal: SessionJournal) -> None:
        assert await journal.find_unfinished() == []


# ── Driver integration: the journal sees everything the model sees ──────


def _make_driver(
    tmp_path: Path,
    responses: list[LLMResponse],
    tool_execute: object = None,
    edn: str = SESSION_PROGRAM_EDN,
    is_human_sender: Callable[[str], bool] = lambda address: True,
) -> tuple[LoopDriver, SessionJournal, MagicMock, MagicMock]:
    spec = MemberSpec.from_edn(loads(edn))
    validate_member_spec(spec)
    rule = spec.rules[0]

    services = MagicMock()
    services.logger = MagicMock()
    services.publish_event = AsyncMock()
    services.build_context = AsyncMock(
        return_value=BuiltContext(messages=[{"role": "user", "content": "hi"}], anchor_len=1)
    )
    services.think = AsyncMock(side_effect=list(responses))
    services.session_span = lambda session: contextlib.nullcontext()

    executor = MagicMock()
    receipt = MagicMock()
    receipt.to_dict.return_value = {"mail_id": "submitted", "message": "入箱成功"}
    executor.send_mail = AsyncMock(return_value=receipt)
    executor.publish = AsyncMock()

    tool_executor = MagicMock()
    tool_executor.execute = AsyncMock(
        side_effect=tool_execute
        or (
            lambda name, args: ToolResult(
                success=True,
                data={"scheduling_request": {"action": "finish", "reason": ""}}
                if name == "finishTurn"
                else {"ok": name},
            )
        )
    )

    on_complete = MagicMock()
    on_complete.run = AsyncMock()

    journal = SessionJournal("agent1", TlonPaths(home=tmp_path))
    driver = LoopDriver(
        identity=AgentIdentity(id="agent1", name="Agent", address="agent1@tlon.internal"),
        recipe=recipe_for(rule),
        services=services,
        executor=executor,
        tool_executor=tool_executor,
        per_tool_timeout=5.0,
        journal=journal,
        spill=ToolSpill(TlonPaths(home=tmp_path), "agent1"),
        # Default context window: the threshold is never crossed here.
        pressure=ContextPressure(MagicMock(), journal, 262144),
        is_human_sender=is_human_sender,
    )
    return driver, journal, executor, services


async def _drive(driver: LoopDriver, session: AgentSession) -> None:
    """Step/collect until completion (mirrors LoopDriver.process_message)."""
    while not session.is_completed:
        if session.state == SessionState.WAITING:
            await asyncio.sleep(0)
            await driver.collect_session_results(session)
            if session.has_pending_tools:
                pending = list(session.get_pending_tasks().values())
                await asyncio.gather(*pending, return_exceptions=True)
                await driver.collect_session_results(session)
        elif session.state == SessionState.ACTIVE:
            await driver.step_session(session)
        else:
            break


class TestDriverJournaling:
    async def test_first_step_journals_explicit_anchor_len(self, tmp_path: Path) -> None:
        """The session/start event carries the anchor length explicitly
        (ADR 0017): the journaled context is complete (model-visible ⇒
        journaled), but the trailing knowledge message is outside the
        anchor."""
        mail = _mail()
        response = LLMResponse(
            content="done",
            tool_calls=[_tc("c1", "finishTurn", {})],
        )
        driver, journal, _, services = _make_driver(tmp_path, [response])
        services.build_context = AsyncMock(
            return_value=BuiltContext(
                messages=[
                    {"role": "user", "content": TRIGGER},
                    {"role": "user", "content": "[已知信息]\n\nclaim"},
                ],
                anchor_len=1,
            )
        )
        session = _session(f"agent1-{mail.id}", mail)

        await _drive(driver, session)

        assert session.anchor_len == 1
        # The initial two messages head the (grown) session context.
        assert session.context[:2] == [
            {"role": "user", "content": TRIGGER},
            {"role": "user", "content": "[已知信息]\n\nclaim"},
        ]
        start = next(
            event
            for event in await journal.read_events(session.id)
            if event.get("type") == "session/start"
        )
        assert start["anchor_len"] == 1
        assert len(start["context"]) == 2  # knowledge included — model saw it

    async def test_responsibility_snapshot_journaled(self, tmp_path: Path) -> None:
        """ADR 0028: session/start persists the responsibility-set id
        snapshot, so crash recovery can read it back for the program-continuation
        comparison."""
        mail = _mail()
        response = LLMResponse(
            content="done",
            tool_calls=[_tc("c1", "finishTurn", {})],
        )
        driver, journal, _, services = _make_driver(tmp_path, [response])
        session = _session("agent1-resp", mail)

        await _drive(driver, session)

        start = next(
            event
            for event in await journal.read_events(session.id)
            if event.get("type") == "session/start"
        )
        assert start["responsibility"] == [mail.id]
        assert "mail_id" not in start
        # The context builder got the wake text + responsibility set.
        services.build_context.assert_awaited_once_with(TRIGGER, [mail])

    async def test_full_session_journal_matches_context(self, tmp_path: Path) -> None:
        mail = _mail()
        response = LLMResponse(
            content="done",
            tool_calls=[_tc("c1", "finishTurn", {})],
        )

        async def tool_execute(name: str, args: dict, context: Any = None) -> ToolResult:
            return ToolResult(
                success=True, data={"scheduling_request": {"action": "finish", "reason": ""}}
            )

        driver, journal, executor, _ = _make_driver(tmp_path, [response], tool_execute=tool_execute)
        session = _session(f"agent1-{mail.id}", mail)

        await _drive(driver, session)

        assert session.is_completed
        # The invariant: everything the model saw is in the log.
        assert await journal.derive_context(session.id) == session.context
        assert await journal.end_reason(session.id) == "complete"
        executor.send_mail.assert_not_awaited()
        # ADR 0009: no interaction save — the journal is the truth; the
        # batch summary trigger reads it at the scheduler's reap point.

    async def test_journal_write_failure_leaves_memory_untouched(self, tmp_path: Path) -> None:
        driver, journal, _, _ = _make_driver(tmp_path, [])
        session = _session("s1")

        async def boom(session_id: str, event_type: str, **payload: object) -> None:
            raise OSError("disk full")

        journal.append = boom  # type: ignore[method-assign]

        with pytest.raises(OSError, match="disk full"):
            await driver._apply_outcome(
                session, None, StageOutcome(context_appends=[{"role": "user", "content": "x"}])
            )
        assert session.context == []  # no journal, no mutation

    async def test_oversized_tool_output_spills_before_journaling(self, tmp_path: Path) -> None:
        """Spill (harness-review item 2): journal and context hold the
        locator; the payload lives in the workspace spill file."""
        from tlon.tools.spill import SPILL_THRESHOLD_CHARS

        mail = _mail()
        big_stdout = "y" * (SPILL_THRESHOLD_CHARS * 2)
        round1 = LLMResponse(
            content="working", tool_calls=[_tc("c1", "executeBashCommand", {"command": "big"})]
        )
        round2 = LLMResponse(
            content="done",
            tool_calls=[_tc("c2", "finishTurn", {})],
        )

        async def tool_execute(name: str, args: dict, context: Any = None) -> ToolResult:
            if name == "finishTurn":
                return ToolResult(
                    success=True,
                    data={"scheduling_request": {"action": "finish", "reason": ""}},
                )
            return ToolResult(success=True, data={"stdout": big_stdout})

        driver, journal, _, _ = _make_driver(tmp_path, [round1, round2], tool_execute=tool_execute)
        session = _session(f"agent1-{mail.id}", mail)

        await _drive(driver, session)

        assert session.is_completed
        # The journaled tool/result is the locator, not the payload.
        replay = await journal.resume(session.id)
        assert replay is not None
        entry = replay.all_tool_results[0]
        locator = entry["result"].data
        assert locator["_spilled"] is True
        assert locator["spill_path"] == f"spills/{session.id}/c1.json"
        # The model's context holds the locator too (invariant preserved).
        assert await journal.derive_context(session.id) == session.context
        assert big_stdout not in json.dumps(session.context)
        # The full payload is on disk, readable as JSON.
        spill_file = tmp_path / "agent1" / "workspace" / "spills" / session.id / "c1.json"
        assert json.loads(spill_file.read_text(encoding="utf-8"))["stdout"] == big_stdout


# ── Crash recovery: orphan tools resume through resume_collect ──────────


class TestCrashRecovery:
    async def test_orphan_tools_resume_to_completion(self, tmp_path: Path) -> None:
        mail = _mail()
        sid = f"agent1-{mail.id}"
        fast, slow = _tc("c-fast"), _tc("c-slow", "slowTool")
        round1 = LLMResponse(content="working", tool_calls=[fast, slow])
        round2 = LLMResponse(
            content="done",
            tool_calls=[_tc("c2", "finishTurn", {})],
        )

        executed: list[str] = []
        never = asyncio.Event()

        async def tool_execute(name: str, args: dict, context: Any = None) -> ToolResult:
            executed.append(name)
            if name == "slowTool":
                await never.wait()  # in-flight when the "process" dies
            if name == "finishTurn":
                return ToolResult(
                    success=True,
                    data={"scheduling_request": {"action": "finish", "reason": ""}},
                )
            return ToolResult(success=True, data={"ok": name})

        driver, journal, executor, _ = _make_driver(
            tmp_path, [round1, round2], tool_execute=tool_execute
        )
        session = _session(sid, mail)

        # Round 1: dispatch both tools; only the fast one finishes.
        await driver.step_session(session)
        assert session.state == SessionState.WAITING
        await session.get_pending_tasks()["c-fast"]
        await session.cancel_pending()  # simulate the process dying

        # Resume from the journal alone.
        replay = await journal.resume(sid)
        assert replay is not None and not replay.completed
        by_id = {tc.id: result for tc, result in replay.pending_collect}
        assert by_id["c-fast"].success is True
        assert by_id["c-slow"].error == INTERRUPTED_ERROR

        scheduler = _make_scheduler(journal, loop_driver=driver)
        resumed = await scheduler._restore_session(replay)

        # First collect after resume consumes the journaled results.
        await driver.resume_collect(resumed, replay.pending_collect)
        assert resumed.state == SessionState.ACTIVE
        assert resumed.iteration == 1
        assert len(resumed.all_tool_results) == 2

        # The session continues to completion — nothing is re-executed.
        await _drive(driver, resumed)
        assert resumed.is_completed
        assert executed.count("someTool") == 1  # replayed, not re-run
        assert executed.count("finishTurn") == 1
        executor.send_mail.assert_not_awaited()
        # The healed log replays cleanly end to end.
        final = await journal.resume(sid)
        assert final is not None and final.completed and final.end_reason == "complete"
        assert final.pending_collect == []
        assert await journal.derive_context(sid) == resumed.context

    async def test_restore_reads_back_responsibility(
        self, tmp_path: Path, tlon: TlonPaths, journal: SessionJournal
    ) -> None:
        """ADR 0028: a crashed session's responsibility ids come back from
        the journal and resolve to Mail objects through the mailbox, so
        the failure-reply anchors and the program continuation survive
        the crash."""
        mailbox = Mailbox(tmp_path / "mail.sqlite3", "agent1")
        mail = _mail()
        await mailbox.append_inbox(mail)
        sid = "agent1-crashed"
        await _start(journal, sid, responsibility=[mail.id])
        scheduler = _make_scheduler(journal, mailbox=mailbox)

        pending = await scheduler._restore_unfinished()

        assert pending is None  # no orphan dispatches → no resume_collect
        session = scheduler._session
        assert session is not None and session.id == sid
        assert [m.id for m in session.responsibility] == [mail.id]


# ── Boot recovery and explicit session invocations (ADR 0028) ───────────────────────────


def _make_scheduler(
    journal: SessionJournal,
    loop_driver: object = None,
    config: AgentConfig | None = None,
    mailbox: Mailbox | None = None,
) -> SessionScheduler:
    scheduler = SessionScheduler(
        config or AgentConfig(id="agent1", name="agent1"),
        memory=MagicMock(),
        session_span=lambda session: contextlib.nullcontext(),
        check_reflection=AsyncMock(),
        note_interaction=MagicMock(),
        make_driver=lambda recipe: loop_driver or MagicMock(),
        journal=journal,
        mailbox=mailbox,
    )
    scheduler._loop_driver = loop_driver or MagicMock()
    return scheduler


class _CompletingDriver:
    """Duck-typed LoopDriver: completes each session on its first step."""

    def __init__(self) -> None:
        self.sessions: list[AgentSession] = []
        self.send_failure_reply = AsyncMock()

    async def step_session(self, session: AgentSession) -> None:
        self.sessions.append(session)
        session.mark_completed()

    async def collect_session_results(self, session: AgentSession) -> None: ...

    async def resume_collect(self, session: AgentSession, completed: list) -> None: ...


async def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out waiting for condition")


class TestBootRecovery:
    async def test_completed_log_is_not_resumed(self, journal: SessionJournal) -> None:
        """Committed end metadata keeps boot from replaying a completed session."""
        sid = "agent1-done"
        await _start(journal, sid, responsibility=["m1"])
        await journal.append(sid, "session/end", reason="complete")
        journal.resume = AsyncMock(  # type: ignore[method-assign]
            side_effect=AssertionError("boot replayed a completed session")
        )
        scheduler = _make_scheduler(journal)

        pending = await scheduler._restore_unfinished()

        assert pending is None
        assert scheduler._session is None
        journal.resume.assert_not_awaited()

    async def test_unfinished_log_resumes_with_pending_collect(
        self, journal: SessionJournal
    ) -> None:
        sid = "agent1-unfinished"
        await _start(journal, sid, responsibility=["m9"])
        await journal.append(sid, "tool/dispatch", tool_calls=[_tc("c1").to_dict()])
        await journal.append(sid, "state", value="waiting")
        scheduler = _make_scheduler(journal)

        pending = await scheduler._restore_unfinished()

        session = scheduler._session
        assert session is not None and session.id == sid
        assert session.context == [{"role": "user", "content": "hi"}]
        assert session.carrier == {"trace_id": "t-1"}  # journaled carrier wins
        assert session.state == SessionState.WAITING
        assert pending is not None and len(pending) == 1
        assert pending[0][1].error == INTERRUPTED_ERROR

    async def test_end_session_journals_reason(self, journal: SessionJournal) -> None:
        scheduler = _make_scheduler(journal)
        session = _session("s-timeout")

        await scheduler._end_session(session, "timeout")

        assert session.is_completed
        assert await journal.end_reason("s-timeout") == "timeout"

    async def test_ensure_session_end_is_idempotent(self, journal: SessionJournal) -> None:
        scheduler = _make_scheduler(journal)
        session = _session("s-end")

        await scheduler._ensure_session_end(session)
        await scheduler._ensure_session_end(session)

        assert await journal.end_reason("s-end") == "complete"
        events = await journal.read_events("s-end")
        ends = [event for event in events if event["type"] == "session/end"]
        assert len(ends) == 1


# ── Abnormal termination: the responsibility set's humans get an answer ──
# (ADR 0027, anchor revised by ADR 0028 决策 7)


class TestFailureReply:
    async def test_send_failure_reply_timeout_and_error(self, tmp_path: Path) -> None:
        driver, _, executor, _ = _make_driver(tmp_path, [])
        mail = _mail()
        session = _session("s1", mail)

        await driver.send_failure_reply(session, "timeout")

        effect = executor.send_mail.await_args.args[0]
        assert effect.to == ["user@tlon.internal"]
        assert effect.in_reply_to == mail.id
        assert effect.thread_id == mail.thread_id
        assert "超时" in effect.content

        executor.send_mail.reset_mock()
        await driver.send_failure_reply(session, "error")

        effect = executor.send_mail.await_args.args[0]
        assert effect.to == ["user@tlon.internal"]
        assert effect.in_reply_to == mail.id
        assert "错误" in effect.content

    async def test_failure_reply_fans_out_to_human_senders_deduped(self, tmp_path: Path) -> None:
        """ADR 0028 决策 7: one mail per human sender in the responsibility
        set (deduped by address; in_reply_to = that sender's latest mail
        in the set)."""
        driver, _, executor, _ = _make_driver(tmp_path, [])
        older = _mail("第一个请求")
        newer = Mail.create(
            from_address="user@tlon.internal",
            content="补充一下",
            to=["agent1@tlon.internal"],
            in_reply_to=older.id,
            thread_id=older.thread_id,
        )
        other = _mail("另一个请求", sender="boss@tlon.internal")
        session = _session("s1", older, newer, other)

        await driver.send_failure_reply(session, "timeout")

        assert executor.send_mail.await_count == 2
        by_recipient = {c.args[0].to[0]: c.args[0] for c in executor.send_mail.await_args_list}
        assert by_recipient["user@tlon.internal"].in_reply_to == newer.id
        assert by_recipient["user@tlon.internal"].thread_id == older.thread_id
        assert by_recipient["boss@tlon.internal"].in_reply_to == other.id

    async def test_failure_reply_suppressed_for_agent_senders(self, tmp_path: Path) -> None:
        """Agent senders in the responsibility set keep the ADR 0027
        suppression: journaled + logged, no mail. Human senders in the
        same set still get theirs."""
        driver, _, executor, _ = _make_driver(
            tmp_path, [], is_human_sender=lambda address: address == "user@tlon.internal"
        )
        agent_mail = _mail("来自另一个 agent", sender="other-agent@tlon.internal")
        human_mail = _mail("来自人类")
        session = _session("s1", agent_mail, human_mail)

        await driver.send_failure_reply(session, "error")

        executor.send_mail.assert_awaited_once()
        assert executor.send_mail.await_args.args[0].to == ["user@tlon.internal"]

    async def test_send_failure_reply_uses_member_replies(self, tmp_path: Path) -> None:
        """A member declaring session :replies gets its own wording on the
        abnormal-termination paths (timeout / stage error)."""
        driver, _, executor, _ = _make_driver(tmp_path, [], edn=REPLIES_EDN)
        session = _session("s1", _mail())

        await driver.send_failure_reply(session, "timeout")

        assert executor.send_mail.await_args.args[0].content == "超时了。"

        executor.send_mail.reset_mock()
        await driver.send_failure_reply(session, "error")

        assert executor.send_mail.await_args.args[0].content == "出错了。"


# ── Deterministic resource-limit notices ──────────────────────────────


class TestResourceLimitNotice:
    async def test_drafts_and_feedback_cannot_claim_submitted_mail(self, tmp_path: Path) -> None:
        mail = _mail()
        driver, journal, executor, services = _make_driver(
            tmp_path, [LLMResponse(content="前函已经发送，现更正错误") for _ in range(10)]
        )
        session = _session(f"agent1-{mail.id}", mail)
        session.loop_scratch["critique_feedback"] = "前函错误"

        await _drive(driver, session)

        assert session.is_completed
        assert await journal.end_reason(session.id) == "max_iterations"
        executor.send_mail.assert_awaited_once()
        effect = executor.send_mail.await_args.args[0]
        assert effect.to == [mail.from_address]
        assert effect.in_reply_to == mail.id
        assert effect.thread_id == mail.thread_id
        assert effect.subject == "运行时通知：处理达到资源上限"
        assert "已确认提交邮局：0 封。" in effect.content
        assert "本次任务未完成" in effect.content
        assert "前函" not in effect.content
        assert services.think.await_count == 10
        services.distill.assert_not_called()

    async def test_member_hint_cannot_replace_factual_report(self, tmp_path: Path) -> None:
        mail = _mail()
        driver, _, executor, _ = _make_driver(
            tmp_path, [LLMResponse(content="working") for _ in range(10)], edn=REPLIES_EDN
        )
        session = _session(f"agent1-{mail.id}", mail)
        await _drive(driver, session)
        content = executor.send_mail.await_args.args[0].content
        assert content.startswith("[运行时通知：处理达到资源上限]")
        assert "已确认提交邮局：0 封。" in content
        assert "附加提示（成员预设文案，不属于执行记录）：想太久了，简单说。" in content


class TestTokenBudget:
    """Session token budget (ADR 0023): every stage LLM call's usage is
    journaled (absolute total) and accumulated on the session; the
    engine's second hard ceiling ends the session with a deterministic
    notice, with reason ``max_tokens``."""

    async def test_stage_llm_usage_accumulates(self, tmp_path: Path) -> None:
        mail = _mail()
        # Round 1: usage missing entirely -> counts as zero, no event.
        # Round 2: prompt+completion fallback (no total_tokens field).
        # Round 3: total_tokens wins, and completes the session.
        round1 = LLMResponse(content="working")
        round2 = LLMResponse(
            content="still working",
            usage={"prompt_tokens": 100, "completion_tokens": 50},
        )
        round3 = LLMResponse(
            content="done",
            tool_calls=[_tc("c1", "finishTurn", {})],
            usage={"prompt_tokens": 600, "completion_tokens": 100, "total_tokens": 700},
        )

        async def tool_execute(name: str, args: dict, context: Any = None) -> ToolResult:
            return ToolResult(
                success=True, data={"scheduling_request": {"action": "finish", "reason": ""}}
            )

        driver, journal, _, _ = _make_driver(
            tmp_path, [round1, round2, round3], tool_execute=tool_execute
        )
        session = _session(f"agent1-{mail.id}", mail)

        await _drive(driver, session)

        assert session.is_completed
        assert session.token_usage == 850
        # Journaled values are the post-accumulation absolute totals; the
        # usage-less first round produced no event at all.
        token_events = [
            event["value"]
            for event in await journal.read_events(session.id)
            if event["type"] == "tokens"
        ]
        assert token_events == [150, 850]
        replay = await journal.resume(session.id)
        assert replay is not None and replay.token_usage == 850

    async def test_max_tokens_sends_factual_notice_without_extra_llm(self, tmp_path: Path) -> None:
        mail = _mail()
        edn = SESSION_PROGRAM_EDN.replace(":max-tokens 1000000", ":max-tokens 1000")
        driver, journal, executor, services = _make_driver(
            tmp_path,
            [LLMResponse(content="已发送三封邮件", usage={"total_tokens": 1500})],
            edn=edn,
        )
        session = _session(f"agent1-{mail.id}", mail)
        await _drive(driver, session)
        assert session.is_completed
        assert services.think.await_count == 1
        services.distill.assert_not_called()
        assert session.token_usage == 1500
        assert await journal.end_reason(session.id) == "max_tokens"
        executor.send_mail.assert_awaited_once()
        effect = executor.send_mail.await_args.args[0]
        assert effect.to == [mail.from_address]
        assert effect.in_reply_to == mail.id
        assert "累计 token 预算已用尽（max_tokens）" in effect.content
        assert "已确认提交邮局：0 封。" in effect.content
        assert "三封" not in effect.content


class TestConsecutiveErrorLimit:
    """error_count counts *consecutive* think failures, not a lifetime
    total: a successful think resets it (docs/design/runtime.md).
    Scattered LLM
    errors across a long session must not kill it; only an unbroken
    streak of max_consecutive_errors failures terminates via until."""

    async def test_scattered_errors_do_not_terminate(self, tmp_path: Path) -> None:
        mail = _mail()
        working = LLMResponse(content="working")  # no tool calls: act advances
        final = LLMResponse(
            content="done",
            tool_calls=[_tc("c-final", "finishTurn", {})],
        )

        async def tool_execute(name: str, args: dict, context: Any = None) -> ToolResult:
            return ToolResult(
                success=True, data={"scheduling_request": {"action": "finish", "reason": ""}}
            )

        # err - ok - err - ok - err - ok: three errors, never consecutive.
        driver, journal, executor, services = _make_driver(
            tmp_path,
            [
                RuntimeError("llm down 1"),
                working,
                RuntimeError("llm down 2"),
                working,
                RuntimeError("llm down 3"),
                final,
            ],
            tool_execute=tool_execute,
        )
        session = _session(f"agent1-{mail.id}", mail)

        await _drive(driver, session)

        assert session.is_completed
        assert await journal.end_reason(session.id) == "complete"
        # Each success reset the streak; the session ends at zero.
        assert session.error_count == 0
        # The reset is journaled (absolute value), so replay agrees.
        replay = await journal.resume(session.id)
        assert replay is not None and replay.error_count == 0
        error_events = [
            event["value"]
            for event in await journal.read_events(session.id)
            if event["type"] == "errors"
        ]
        assert error_events == [1, 0, 1, 0, 1, 0]

    async def test_consecutive_errors_terminate(self, tmp_path: Path) -> None:
        mail = _mail()
        driver, journal, executor, services = _make_driver(
            tmp_path,
            [RuntimeError("llm down")] * 3 + [LLMResponse(content="never reached")],
        )
        session = _session(f"agent1-{mail.id}", mail)

        await _drive(driver, session)

        assert session.is_completed
        assert session.error_count == 3
        assert services.think.await_count == 3  # the 4th response is never consumed
        assert await journal.end_reason(session.id) == "until"
        executor.send_mail.assert_awaited_once()
        effect = executor.send_mail.await_args.args[0]
        assert effect.to == ["user@tlon.internal"]
        assert effect.in_reply_to == mail.id
        assert "错误" in effect.content


class TestTerminationReplyOverrides:
    """`session :replies` overrides the `_terminate_loop` wording per kind;
    undeclared kinds keep the engine's built-in texts."""

    async def test_error_limit_uses_member_reply(self, tmp_path: Path) -> None:
        mail = _mail()
        driver, journal, executor, _ = _make_driver(
            tmp_path,
            [RuntimeError("llm down")] * 3,
            edn=REPLIES_EDN,
        )
        session = _session(f"agent1-{mail.id}", mail)

        await _drive(driver, session)

        assert session.is_completed
        assert session.error_count == 3
        assert await journal.end_reason(session.id) == "until"
        executor.send_mail.assert_awaited_once()
        assert executor.send_mail.await_args.args[0].content == "出错了。"

    async def test_iteration_limit_uses_member_reply(self, tmp_path: Path) -> None:
        """An until that fires below the hard ceiling terminates via
        `_terminate_loop`'s iteration-limit branch — custom wording."""
        mail = _mail()
        edn = REPLIES_EDN.replace(
            ":until (or (iteration-limit-reached?) (error-limit-reached?))",
            ":until (not (iteration-limit-reached?))",
        )
        driver, journal, executor, _ = _make_driver(tmp_path, [], edn=edn)
        session = _session(f"agent1-{mail.id}", mail)

        await _drive(driver, session)

        assert session.is_completed
        assert await journal.end_reason(session.id) == "until"
        executor.send_mail.assert_awaited_once()
        assert executor.send_mail.await_args.args[0].content == "想太久了，简单说。"


class TestTerminationReplyHumanOnly:
    """ADR 0027 (anchor revised by ADR 0028 决策 7): termination replies go
    to the responsibility set's human senders only. A session whose
    responsibility is all non-human senders (agent / postmaster /
    external address) ends journaled + logged, without mail — breaking
    the failure -> reply -> retrigger oscillation between agents."""

    @staticmethod
    def _human_only(address: str) -> bool:
        return address == "user@tlon.internal"

    @staticmethod
    def _agent_mail() -> Mail:
        return Mail.create(
            from_address="other-agent@tlon.internal",
            content="hi",
            to=["agent1@tlon.internal"],
        )

    async def test_error_limit_suppressed_for_agent_sender(self, tmp_path: Path) -> None:
        mail = self._agent_mail()
        driver, journal, executor, _ = _make_driver(
            tmp_path,
            [RuntimeError("llm down")] * 3,
            is_human_sender=self._human_only,
        )
        session = _session(f"agent1-{mail.id}", mail)

        await _drive(driver, session)

        assert session.is_completed
        assert session.error_count == 3
        # The termination is journaled exactly as before — only the mail
        # is suppressed.
        assert await journal.end_reason(session.id) == "until"
        executor.send_mail.assert_not_awaited()

    async def test_error_limit_sent_for_human_sender(self, tmp_path: Path) -> None:
        mail = _mail()  # user@tlon.internal — human
        driver, journal, executor, _ = _make_driver(
            tmp_path,
            [RuntimeError("llm down")] * 3,
            is_human_sender=self._human_only,
        )
        session = _session(f"agent1-{mail.id}", mail)

        await _drive(driver, session)

        assert session.is_completed
        assert await journal.end_reason(session.id) == "until"
        executor.send_mail.assert_awaited_once()
        effect = executor.send_mail.await_args.args[0]
        assert effect.to == ["user@tlon.internal"]
        assert "错误" in effect.content

    async def test_failure_reply_suppressed_for_postmaster_and_external(
        self, tmp_path: Path
    ) -> None:
        driver, _, executor, _ = _make_driver(tmp_path, [], is_human_sender=self._human_only)

        for sender in ("postmaster@tlon.internal", "notifications@github.com"):
            mail = Mail.create(from_address=sender, content="bounce", to=["agent1@tlon.internal"])
            await driver.send_failure_reply(_session("s1", mail), "timeout")
            await driver.send_failure_reply(_session("s2", mail), "error")

        executor.send_mail.assert_not_awaited()

    async def test_hard_ceiling_suppression_skips_notification(self, tmp_path: Path) -> None:
        """Agent-only responsibility sets never receive runtime mail."""
        mail = self._agent_mail()
        driver, journal, executor, services = _make_driver(
            tmp_path,
            [LLMResponse(content=f"working {i}") for i in range(10)],
            is_human_sender=self._human_only,
        )
        session = _session(f"agent1-{mail.id}", mail)

        await _drive(driver, session)

        assert session.is_completed
        assert await journal.end_reason(session.id) == "max_iterations"
        services.distill.assert_not_called()
        executor.send_mail.assert_not_awaited()


class TestSchedulerFailureReply:
    """Session timeout / stage error both notify the responsibility set's
    human senders before the reap (the scheduler owns the fatal paths;
    the reply goes through LoopDriver.send_failure_reply)."""

    @staticmethod
    async def _wait_reaped(scheduler: SessionScheduler) -> None:
        await _wait_for(lambda: scheduler.active_session_count == 0)

    async def test_timeout_ends_session_and_replies(self, journal: SessionJournal) -> None:
        mail = _mail()
        loop_driver = MagicMock()
        loop_driver.send_failure_reply = AsyncMock()
        scheduler = _make_scheduler(
            journal,
            loop_driver=loop_driver,
            config=AgentConfig(id="agent1", name="agent1", session_timeout_seconds=0),
        )

        recipe = recipe_for(MemberSpec.from_edn(loads(SESSION_PROGRAM_EDN)).rules[0])
        scheduler.start()
        await scheduler.wait_recovered()
        invocation = asyncio.create_task(scheduler.run_invocation(recipe, TRIGGER, [mail], {}, {}))
        try:
            await _wait_for(lambda: loop_driver.send_failure_reply.await_count == 1)
            await self._wait_reaped(scheduler)
        finally:
            await invocation
            await scheduler.stop()

        session, reason = loop_driver.send_failure_reply.await_args.args
        assert reason == "timeout"
        assert [m.id for m in session.responsibility] == [mail.id]
        assert await journal.end_reason(session.id) == "timeout"

    async def test_stage_error_ends_session_and_replies(self, journal: SessionJournal) -> None:
        mail = _mail()
        loop_driver = MagicMock()
        loop_driver.send_failure_reply = AsyncMock()
        loop_driver.step_session = AsyncMock(side_effect=RuntimeError("boom"))
        scheduler = _make_scheduler(journal, loop_driver=loop_driver)

        recipe = recipe_for(MemberSpec.from_edn(loads(SESSION_PROGRAM_EDN)).rules[0])
        scheduler.start()
        await scheduler.wait_recovered()
        invocation = asyncio.create_task(scheduler.run_invocation(recipe, TRIGGER, [mail], {}, {}))
        try:
            await _wait_for(lambda: loop_driver.send_failure_reply.await_count == 1)
            await self._wait_reaped(scheduler)
        finally:
            await invocation
            await scheduler.stop()

        session, reason = loop_driver.send_failure_reply.await_args.args
        assert reason == "error"
        assert await journal.end_reason(session.id) == "error"

    async def test_reply_failure_does_not_break_reap(self, journal: SessionJournal) -> None:
        mail = _mail()
        loop_driver = MagicMock()
        loop_driver.send_failure_reply = AsyncMock(side_effect=OSError("bus down"))
        loop_driver.step_session = AsyncMock(side_effect=RuntimeError("boom"))
        scheduler = _make_scheduler(journal, loop_driver=loop_driver)

        recipe = recipe_for(MemberSpec.from_edn(loads(SESSION_PROGRAM_EDN)).rules[0])
        scheduler.start()
        await scheduler.wait_recovered()
        invocation = asyncio.create_task(scheduler.run_invocation(recipe, TRIGGER, [mail], {}, {}))
        try:
            await _wait_for(lambda: loop_driver.send_failure_reply.await_count == 1)
            await self._wait_reaped(scheduler)
        finally:
            await invocation
            await scheduler.stop()

        # The reply failed, but the session still ended and was reaped.
        session, reason = loop_driver.send_failure_reply.await_args.args
        assert await journal.end_reason(session.id) == "error"


class TestSchedulingIntentJournal:
    async def test_sleep_request_survives_replay_before_completion(self, tmp_path: Path) -> None:
        """A crash after scratch append retains the exact requested UTC deadline."""
        driver, journal, _, _ = _make_driver(tmp_path, [])
        session = _session("agent1-sleep-request")
        await journal.append(session.id, "session/start", context=[], responsibility=[])
        request = {"action": "sleep", "wake_at": "2040-01-01T00:00:00+00:00", "reason": "休息"}
        from tlon.agents.stages.outcome import StageOutcome

        await driver._apply_outcome(
            session, None, StageOutcome(scratch_updates={"scheduling_request": request})
        )
        replay = await journal.resume(session.id)
        assert replay is not None
        assert replay.loop_scratch["scheduling_request"] == request
        assert not replay.completed

    async def test_runtime_intent_is_durable_before_session_end(self, tmp_path: Path) -> None:
        """A failed lifecycle write must not make the journal appear completed."""
        driver, journal, _, _ = _make_driver(tmp_path, [])
        session = _session("agent1-exit-request")
        await journal.append(session.id, "session/start", context=[], responsibility=[])
        from tlon.agents.stages.outcome import StageOutcome

        driver._prepare_scheduling_request = AsyncMock(side_effect=OSError("disk full"))
        request = {"action": "exit", "reason": "done"}
        with pytest.raises(OSError, match="disk full"):
            await driver._apply_outcome(
                session,
                None,
                StageOutcome(
                    scratch_updates={"scheduling_request": request},
                    complete=True,
                ),
            )
        driver._prepare_scheduling_request.assert_awaited_once_with("agent1", request, session.id)
        assert not session.is_completed
        assert await journal.end_reason(session.id) is None
        replay = await journal.resume(session.id)
        assert replay is not None and replay.loop_scratch["scheduling_request"] == request


class TestExplicitInvocationRecovery:
    async def test_recovery_uses_journaled_recipe_and_source(self, journal: SessionJournal) -> None:
        from tlon.agents.member_spec import SessionRecipe

        sid = "agent1-original-program"
        recipe = recipe_for(MemberSpec.from_edn(loads(SESSION_PROGRAM_EDN)).rules[0])
        raw = recipe.to_edn_data()
        from tlosure.edn import Keyword

        raw[Keyword("max-iterations")] = 7
        source = _mail("original source")
        effect = {"run_id": "original-run", "index": 1, "name": "run-loop", "args_edn": "[]"}
        await journal.append(
            sid,
            "session/start",
            agent_id="agent1",
            recipe=dumps(raw),
            source_mail_id=source.id,
            invocation={"rule_name": "second", "program_effect": effect},
            trigger_content="original trigger",
            context=[{"role": "user", "content": "original"}],
        )
        selected: list[SessionRecipe] = []
        driver = _CompletingDriver()
        scheduler = _make_scheduler(journal, loop_driver=driver)

        def make_driver(actual: SessionRecipe) -> Any:
            selected.append(actual)
            return driver

        scheduler._make_driver = make_driver
        scheduler._resume_invocation = AsyncMock()
        scheduler.start()
        try:
            await scheduler.wait_recovered()
            assert len(selected) == 1 and selected[0].max_iterations == 7
            resumed = driver.sessions[0]
            assert resumed.source_mail_id == source.id
            assert resumed.invocation == {"rule_name": "second", "program_effect": effect}
            assert resumed.trigger_content == "original trigger"
            scheduler._resume_invocation.assert_awaited_once_with(resumed)
            assert await journal.end_reason(sid) == "complete"
        finally:
            await scheduler.stop()


async def test_completed_session_records_effect_without_model_reexecution(
    journal: SessionJournal,
) -> None:
    sid = "agent1-completed-before-effect"
    recipe = recipe_for(MemberSpec.from_edn(loads(SESSION_PROGRAM_EDN)).rules[0])
    await journal.append(
        sid,
        "session/start",
        agent_id="agent1",
        recipe=dumps(recipe.to_edn_data()),
        source_mail_id="source-7",
        invocation={
            "rule_name": "publish",
            "program_effect": {
                "run_id": "source-7:publish",
                "index": 0,
                "name": "run-loop",
                "args_edn": "[]",
            },
        },
        trigger_content="source",
        context=[{"role": "user", "content": "source"}],
    )
    await journal.append(sid, "session/end", reason="complete")
    driver = _CompletingDriver()
    scheduler = _make_scheduler(journal, loop_driver=driver)
    scheduler._resume_invocation = AsyncMock()
    scheduler.start()
    try:
        await scheduler.wait_recovered()
        assert driver.sessions == []
        scheduler._resume_invocation.assert_awaited_once()
        resumed = scheduler._resume_invocation.await_args.args[0]
        assert resumed.id == sid and resumed.invocation["rule_name"] == "publish"
    finally:
        await scheduler.stop()


async def test_recovery_chooses_latest_session_for_the_same_program_effect(
    journal: SessionJournal,
) -> None:
    for sid, marker in (("agent1-z-first", "old"), ("agent1-a-second", "latest")):
        await journal.append(
            sid,
            "session/start",
            agent_id="agent1",
            recipe="unused",
            source_mail_id="same-source",
            invocation={
                "rule_name": "publish",
                "marker": marker,
                "program_effect": {
                    "run_id": "same-run",
                    "index": 0,
                    "name": "run-loop",
                    "args_edn": "[]",
                },
            },
            trigger_content="source",
            context=[],
        )
        await journal.append(sid, "session/end", reason="complete")
    scheduler = _make_scheduler(journal)
    scheduler._resume_invocation = AsyncMock()
    scheduler.start()
    try:
        await scheduler.wait_recovered()
        scheduler._resume_invocation.assert_awaited_once()
        resumed = scheduler._resume_invocation.await_args.args[0]
        assert resumed.id == "agent1-a-second"
        assert resumed.invocation["marker"] == "latest"
    finally:
        await scheduler.stop()


@pytest.mark.parametrize("source_mail_id", [None, "same-mail"], ids=["event", "mail"])
async def test_completed_sessions_recover_every_effect_in_same_program(
    journal: SessionJournal,
    tmp_path: Path,
    source_mail_id: str | None,
) -> None:
    from tlon.agents.program_store import ProgramStore

    store = ProgramStore(tmp_path / "program.sqlite3")
    expected: list[dict[str, Any]] = []
    for index in range(2):
        effect = {
            "run_id": "event:publish",
            "index": index,
            "name": "run-loop",
            "args_edn": dumps([{"index": index}]),
        }
        expected.append(effect)
        sid = f"agent1-event-loop-{index}"
        await journal.append(
            sid,
            "session/start",
            agent_id="agent1",
            recipe="unused",
            source_mail_id=source_mail_id,
            invocation={"rule_name": "publish", "program_effect": effect},
            trigger_content="scheduled edition",
            context=[],
        )
        await journal.append(sid, "session/end", reason="complete")
    driver = _CompletingDriver()
    scheduler = _make_scheduler(journal, loop_driver=driver)
    resumed: list[dict[str, Any]] = []

    async def record_result(session: AgentSession) -> None:
        effect = session.invocation["program_effect"]
        store.record_effect(
            effect["run_id"],
            effect["index"],
            effect["name"],
            effect["args_edn"],
            dumps(None),
        )
        resumed.append(effect)

    scheduler._resume_invocation = record_result
    scheduler.start()
    try:
        await scheduler.wait_recovered()
        assert driver.sessions == []
        assert resumed == expected
        for effect in expected:
            assert store.get_effect(
                effect["run_id"],
                effect["index"],
                effect["name"],
                effect["args_edn"],
            ) == (True, "nil")
    finally:
        await scheduler.stop()


async def test_completed_dispatch_skips_completed_session_effect_recovery(
    journal: SessionJournal,
) -> None:
    await journal.append(
        "agent1-already-dispatched",
        "session/start",
        agent_id="agent1",
        recipe="unused",
        source_mail_id="done-mail",
        invocation={
            "rule_name": "publish",
            "program_effect": {
                "run_id": "done-mail:publish",
                "index": 0,
                "name": "run-loop",
                "args_edn": "[]",
            },
        },
        trigger_content="done",
        context=[],
    )
    await journal.append("agent1-already-dispatched", "session/end", reason="complete")
    await journal.append("dispatch:done-mail:publish", "dispatch/complete")
    scheduler = _make_scheduler(journal)
    scheduler._resume_invocation = AsyncMock()
    scheduler.start()
    try:
        await scheduler.wait_recovered()
        scheduler._resume_invocation.assert_not_awaited()
    finally:
        await scheduler.stop()


async def test_slow_session_recovery_does_not_block_scheduler_startup(
    journal: SessionJournal,
) -> None:
    recipe = recipe_for(MemberSpec.from_edn(loads(SESSION_PROGRAM_EDN)).rules[0])
    await journal.append(
        "agent1-slow-recovery",
        "session/start",
        agent_id="agent1",
        recipe=dumps(recipe.to_edn_data()),
        trigger_content="restore",
        context=[],
        invocation={
            "rule_name": "publish",
            "program_effect": {
                "run_id": "restore-program",
                "index": 0,
                "name": "run-loop",
                "args_edn": "[]",
            },
        },
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    class SlowRecoveryDriver(_CompletingDriver):
        async def step_session(self, session: AgentSession) -> None:
            entered.set()
            await release.wait()
            await super().step_session(session)

    scheduler = _make_scheduler(journal, loop_driver=SlowRecoveryDriver())
    scheduler._resume_invocation = AsyncMock()
    scheduler.start()
    waiter = asyncio.create_task(scheduler.wait_recovered())
    try:
        await asyncio.wait_for(scheduler.wait_ready(), 0.5)
        await asyncio.wait_for(entered.wait(), 0.5)
        assert scheduler.ready.is_set()
        assert not scheduler.recovery_ready.is_set()
        assert not waiter.done()
        scheduler._resume_invocation.assert_not_awaited()
        release.set()
        await asyncio.wait_for(waiter, 1)
        scheduler._resume_invocation.assert_awaited_once()
        assert scheduler.recovery_ready.is_set()
    finally:
        release.set()
        waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiter
        await scheduler.stop()


async def test_wait_recovered_propagates_background_recovery_failure(
    journal: SessionJournal,
) -> None:
    scheduler = _make_scheduler(journal)
    scheduler._restore_unfinished = AsyncMock(side_effect=OSError("journal unavailable"))
    scheduler.start()
    assert scheduler._worker_task is not None
    try:
        with pytest.raises(OSError, match="journal unavailable"):
            await asyncio.wait_for(scheduler.wait_recovered(), 0.5)
        assert scheduler.ready.is_set()
        assert scheduler.recovery_ready.is_set()
    finally:
        # The worker has failed; consume that failure without asking stop()
        # to turn an operational error into cancellation.
        with contextlib.suppress(OSError):
            await scheduler._worker_task
