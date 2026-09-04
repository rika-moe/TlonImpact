"""Session journal (ADR 0007): append-only per-session event log + replay.

Pins the append/resume roundtrip, the orphan-dispatch self-healing, the
"model-visible ⇒ journaled" invariant (derive_context == session.context
after a full driver run), crash recovery via resume_collect, and the
scheduler admission rules (completed logs only advance the read cursor),
and the abnormal-termination failure replies (timeout / stage error).
"""

import asyncio
import contextlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.agents.context_pressure import ContextPressure
from tlon.agents.edn import loads
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
from tlon.tools.base import ToolResult
from tlon.tools.spill import ToolSpill

COGNITIVE_EDN = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :loop {:context {:sections [(persona) (protocol)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :reflect {:enabled false :interval-minutes 5}}}]}
"""


@pytest.fixture
def tlon(tmp_path: Path) -> TlonPaths:
    return TlonPaths(home=tmp_path)


@pytest.fixture
def journal(tlon: TlonPaths) -> SessionJournal:
    return SessionJournal("agent1", tlon)


def _mail() -> Mail:
    return Mail.create(
        from_address="user@tlon.internal", content="hi", to=["agent1@tlon.internal"]
    )


def _tc(tc_id: str, name: str = "someTool", arguments: dict | None = None) -> ToolCall:
    return ToolCall(id=tc_id, function_name=name, arguments=arguments or {})


async def _start(journal: SessionJournal, sid: str, mail_id: str = "m1") -> None:
    await journal.append(
        sid,
        "session/start",
        agent_id="agent1",
        mail_id=mail_id,
        trigger_content="hi",
        carrier={"trace_id": "t-1"},
        context=[{"role": "user", "content": "hi"}],
    )


# ── Journal: append / resume ────────────────────────────────────────────


class TestJournalReplay:
    async def test_append_resume_roundtrip(self, journal: SessionJournal) -> None:
        sid = "agent1-deadbeef"
        await _start(journal, sid)
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

    async def test_completed_log_marks_session_done(self, journal: SessionJournal) -> None:
        sid = "agent1-done"
        await _start(journal, sid)
        await journal.append(sid, "session/end", reason="complete")

        replay = await journal.resume(sid)

        assert replay is not None
        assert replay.completed is True
        assert replay.end_reason == "complete"
        assert await journal.end_reason(sid) == "complete"

    async def test_torn_tail_line_is_skipped(
        self, journal: SessionJournal, tlon: TlonPaths
    ) -> None:
        sid = "agent1-torn"
        await _start(journal, sid)
        path = tlon.agent_sessions_dir("agent1") / f"{sid}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write('{"seq": 1, "type": "context/app')  # crash mid-write

        replay = await journal.resume(sid)

        assert replay is not None
        assert replay.context == [{"role": "user", "content": "hi"}]

    async def test_orphan_dispatch_heals_with_interrupted_result(
        self, journal: SessionJournal
    ) -> None:
        sid = "agent1-orphan"
        await _start(journal, sid)
        done, lost = _tc("c-done"), _tc("c-lost", "slowTool")
        await journal.append(
            sid, "tool/dispatch", tool_calls=[done.to_dict(), lost.to_dict()]
        )
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
        raw = [
            json.loads(line)
            for line in (await _read_lines(healed.session_id, journal))
        ]
        synthetic = [
            e
            for e in raw
            if e.get("type") == "tool/result" and e["tool_call"]["id"] == "c-lost"
        ]
        assert len(synthetic) == 1
        assert synthetic[0]["result"]["error"] == INTERRUPTED_ERROR

    async def test_derive_context_rebuilds_model_view(self, journal: SessionJournal) -> None:
        sid = "agent1-ctx"
        await _start(journal, sid)
        await journal.append(sid, "context/append", messages=[{"role": "assistant", "content": "a"}])
        await journal.append(sid, "context/append", messages=[{"role": "tool", "content": "b"}])

        assert await journal.derive_context(sid) == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "a"},
            {"role": "tool", "content": "b"},
        ]


async def _read_lines(session_id: str, journal: SessionJournal) -> list[str]:
    path = journal._path(session_id)
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── Driver integration: the journal sees everything the model sees ──────


def _make_driver(
    tmp_path: Path,
    responses: list[LLMResponse],
    tool_execute: object = None,
) -> tuple[LoopDriver, SessionJournal, MagicMock, MagicMock]:
    spec = MemberSpec.from_edn(loads(COGNITIVE_EDN))
    validate_member_spec(spec)
    rule = spec.rules[0]

    services = MagicMock()
    services.logger = MagicMock()
    services.publish_event = AsyncMock()
    services.build_context = AsyncMock(
        return_value=[{"role": "user", "content": "hi"}]
    )
    services.think = AsyncMock(side_effect=list(responses))
    services.session_span = lambda session: contextlib.nullcontext()

    executor = MagicMock()
    executor.send_mail = AsyncMock()
    executor.publish = AsyncMock()
    executor.mark_mail_read = AsyncMock()

    tool_executor = MagicMock()
    tool_executor.execute = AsyncMock(
        side_effect=tool_execute
        or (lambda name, args: ToolResult(success=True, data={"ok": name}))
    )

    on_complete = MagicMock()
    on_complete.run = AsyncMock()

    journal = SessionJournal("agent1", TlonPaths(home=tmp_path))
    driver = LoopDriver(
        identity=AgentIdentity(id="agent1", name="Agent", address="agent1@tlon.internal"),
        rule=rule,
        services=services,
        executor=executor,
        tool_executor=tool_executor,
        per_tool_timeout=5.0,
        on_complete=on_complete,
        journal=journal,
        spill=ToolSpill(TlonPaths(home=tmp_path), "agent1"),
        # Default context window: the threshold is never crossed here.
        pressure=ContextPressure(MagicMock(), journal, 262144),
    )
    return driver, journal, executor, services


async def _drive(driver: LoopDriver, session: AgentSession, mail: Mail) -> None:
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
            await driver.step_session(session, mail)
        else:
            break


class TestDriverJournaling:
    async def test_full_session_journal_matches_context(self, tmp_path: Path) -> None:
        mail = _mail()
        response = LLMResponse(
            content="done",
            tool_calls=[
                _tc("c1", "sendEmail", {"content": "reply", "is_complete": True})
            ],
        )

        async def tool_execute(name: str, args: dict) -> ToolResult:
            return ToolResult(
                success=True, data={"content": args["content"], "is_complete": True}
            )

        driver, journal, executor, _ = _make_driver(
            tmp_path, [response], tool_execute=tool_execute
        )
        session = AgentSession(
            session_id=f"agent1-{mail.id}", trigger_content=mail.content
        )

        await _drive(driver, session, mail)

        assert session.is_completed
        # The invariant: everything the model saw is in the log.
        assert await journal.derive_context(session.id) == session.context
        assert await journal.end_reason(session.id) == "complete"
        executor.send_mail.assert_awaited_once()
        # ADR 0009: no interaction save — the journal is the truth; the
        # batch summary trigger reads it at the scheduler's reap point.

    async def test_journal_write_failure_leaves_memory_untouched(
        self, tmp_path: Path
    ) -> None:
        driver, journal, _, _ = _make_driver(tmp_path, [])
        session = AgentSession(session_id="s1", trigger_content="trigger")

        async def boom(session_id: str, event_type: str, **payload: object) -> None:
            raise OSError("disk full")

        journal.append = boom  # type: ignore[method-assign]

        with pytest.raises(OSError, match="disk full"):
            await driver._apply_outcome(
                session, None, StageOutcome(context_appends=[{"role": "user", "content": "x"}])
            )
        assert session.context == []  # no journal, no mutation

    async def test_oversized_tool_output_spills_before_journaling(
        self, tmp_path: Path
    ) -> None:
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
            tool_calls=[_tc("c2", "sendEmail", {"content": "final", "is_complete": True})],
        )

        async def tool_execute(name: str, args: dict) -> ToolResult:
            if name == "sendEmail":
                return ToolResult(
                    success=True,
                    data={"content": args["content"], "is_complete": True},
                )
            return ToolResult(success=True, data={"stdout": big_stdout})

        driver, journal, _, _ = _make_driver(
            tmp_path, [round1, round2], tool_execute=tool_execute
        )
        session = AgentSession(
            session_id=f"agent1-{mail.id}", trigger_content=mail.content
        )

        await _drive(driver, session, mail)

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
        spill_file = (
            tmp_path / "agent1" / "workspace" / "spills" / session.id / "c1.json"
        )
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
            tool_calls=[_tc("c2", "sendEmail", {"content": "final", "is_complete": True})],
        )

        executed: list[str] = []
        never = asyncio.Event()

        async def tool_execute(name: str, args: dict) -> ToolResult:
            executed.append(name)
            if name == "slowTool":
                await never.wait()  # in-flight when the "process" dies
            if name == "sendEmail":
                return ToolResult(
                    success=True,
                    data={"content": args["content"], "is_complete": True},
                )
            return ToolResult(success=True, data={"ok": name})

        driver, journal, executor, _ = _make_driver(
            tmp_path, [round1, round2], tool_execute=tool_execute
        )
        session = AgentSession(session_id=sid, trigger_content=mail.content)

        # Round 1: dispatch both tools; only the fast one finishes.
        await driver.step_session(session, mail)
        assert session.state == SessionState.WAITING
        await session.get_pending_tasks()["c-fast"]
        await session.cancel_pending()  # simulate the process dying

        # Resume from the journal alone.
        replay = await journal.resume(sid)
        assert replay is not None and not replay.completed
        by_id = {tc.id: result for tc, result in replay.pending_collect}
        assert by_id["c-fast"].success is True
        assert by_id["c-slow"].error == INTERRUPTED_ERROR

        scheduler, _ = _make_scheduler(journal, loop_driver=driver)
        resumed = scheduler._restore_session(replay, mail, {})

        # First collect after resume consumes the journaled results.
        await driver.resume_collect(resumed, replay.pending_collect)
        assert resumed.state == SessionState.ACTIVE
        assert resumed.iteration == 1
        assert len(resumed.all_tool_results) == 2

        # The session continues to completion — nothing is re-executed.
        await _drive(driver, resumed, mail)
        assert resumed.is_completed
        assert executed.count("someTool") == 1  # replayed, not re-run
        assert executed.count("sendEmail") == 1
        executor.send_mail.assert_awaited_once()
        # The healed log replays cleanly end to end.
        final = await journal.resume(sid)
        assert final is not None and final.completed and final.end_reason == "complete"
        assert final.pending_collect == []
        assert await journal.derive_context(sid) == resumed.context


# ── Scheduler admission rules ───────────────────────────────────────────


def _make_scheduler(
    journal: SessionJournal, loop_driver: object = None
) -> tuple[SessionScheduler, AsyncMock]:
    mark_read = AsyncMock()
    scheduler = SessionScheduler(
        AgentConfig(id="agent1", name="agent1"),
        memory=MagicMock(),
        session_span=lambda session: contextlib.nullcontext(),
        mark_mail_read=mark_read,
        check_reflection=AsyncMock(),
        note_interaction=MagicMock(),
        loop_driver=loop_driver or MagicMock(),
        journal=journal,
    )
    return scheduler, mark_read


class TestSchedulerAdmission:
    async def test_completed_log_only_marks_mail_read(
        self, tmp_path: Path, journal: SessionJournal
    ) -> None:
        mail = _mail()
        sid = f"agent1-{mail.id}"
        await _start(journal, sid, mail_id=mail.id)
        await journal.append(sid, "session/end", reason="complete")
        scheduler, mark_read = _make_scheduler(journal)

        sessions: dict[str, AgentSession] = {}
        mails: dict[str, Mail] = {}
        pending: dict = {}
        await scheduler._admit(mail, {}, sessions, mails, pending)

        assert sessions == {} and mails == {} and pending == {}
        mark_read.assert_awaited_once_with(mail)

    async def test_orphan_log_resumes_session_with_pending_collect(
        self, tmp_path: Path, journal: SessionJournal
    ) -> None:
        mail = _mail()
        sid = f"agent1-{mail.id}"
        await _start(journal, sid, mail_id=mail.id)
        await journal.append(sid, "tool/dispatch", tool_calls=[_tc("c1").to_dict()])
        await journal.append(sid, "state", value="waiting")
        scheduler, mark_read = _make_scheduler(journal)

        sessions: dict[str, AgentSession] = {}
        mails: dict[str, Mail] = {}
        pending: dict = {}
        await scheduler._admit(mail, {"trace_id": "t-9"}, sessions, mails, pending)

        session = sessions[sid]
        assert session.context == [{"role": "user", "content": "hi"}]
        assert session.trigger_message is mail
        assert session.carrier == {"trace_id": "t-1"}  # journaled carrier wins
        assert session.state == SessionState.WAITING
        assert mails[sid] is mail
        assert len(pending[sid]) == 1
        assert pending[sid][0][1].error == INTERRUPTED_ERROR
        mark_read.assert_not_awaited()

    async def test_end_session_journals_reason(self, journal: SessionJournal) -> None:
        scheduler, _ = _make_scheduler(journal)
        session = AgentSession(session_id="s-timeout", trigger_content="t")

        await scheduler._end_session(session, "timeout")

        assert session.is_completed
        assert await journal.end_reason("s-timeout") == "timeout"

    async def test_ensure_session_end_is_idempotent(self, journal: SessionJournal) -> None:
        scheduler, _ = _make_scheduler(journal)
        session = AgentSession(session_id="s-end", trigger_content="t")

        await scheduler._ensure_session_end(session)
        await scheduler._ensure_session_end(session)

        assert await journal.end_reason("s-end") == "complete"
        lines = await _read_lines("s-end", journal)
        ends = [json.loads(line) for line in lines if json.loads(line)["type"] == "session/end"]
        assert len(ends) == 1


# ── Abnormal termination: the sender always gets an answer ──────────────


class TestFailureReply:
    async def test_send_failure_reply_timeout_and_error(self, tmp_path: Path) -> None:
        driver, _, executor, _ = _make_driver(tmp_path, [])
        mail = _mail()

        await driver.send_failure_reply(mail, "timeout")

        effect = executor.send_mail.await_args.args[0]
        assert effect.to == ["user@tlon.internal"]
        assert effect.in_reply_to == mail.id
        assert effect.thread_id == mail.thread_id
        assert "超时" in effect.content

        executor.send_mail.reset_mock()
        await driver.send_failure_reply(mail, "error")

        effect = executor.send_mail.await_args.args[0]
        assert effect.to == ["user@tlon.internal"]
        assert effect.in_reply_to == mail.id
        assert "错误" in effect.content


class TestSchedulerFailureReply:
    """Session timeout / stage error both reply to the trigger mail's
    sender before the reap (the scheduler owns the fatal paths; the
    reply goes through LoopDriver.send_failure_reply)."""

    @staticmethod
    async def _wait_reaped(mark_read: AsyncMock) -> None:
        for _ in range(500):
            if mark_read.await_count:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("session was not reaped in time")

    async def test_timeout_ends_session_and_replies(
        self, journal: SessionJournal
    ) -> None:
        mail = _mail()
        loop_driver = MagicMock()
        loop_driver.send_failure_reply = AsyncMock()
        scheduler, mark_read = _make_scheduler(journal, loop_driver=loop_driver)
        scheduler._config.session_timeout_seconds = 0

        scheduler.enqueue(mail, {})
        scheduler.start()
        try:
            await self._wait_reaped(mark_read)
        finally:
            await scheduler.stop()

        loop_driver.send_failure_reply.assert_awaited_once()
        assert loop_driver.send_failure_reply.await_args.args == (mail, "timeout")
        assert await journal.end_reason(f"agent1-{mail.id}") == "timeout"
        mark_read.assert_awaited_once_with(mail)

    async def test_stage_error_ends_session_and_replies(
        self, journal: SessionJournal
    ) -> None:
        mail = _mail()
        loop_driver = MagicMock()
        loop_driver.send_failure_reply = AsyncMock()
        loop_driver.step_session = AsyncMock(side_effect=RuntimeError("boom"))
        scheduler, mark_read = _make_scheduler(journal, loop_driver=loop_driver)

        scheduler.enqueue(mail, {})
        scheduler.start()
        try:
            await self._wait_reaped(mark_read)
        finally:
            await scheduler.stop()

        loop_driver.send_failure_reply.assert_awaited_once()
        assert loop_driver.send_failure_reply.await_args.args == (mail, "error")
        assert await journal.end_reason(f"agent1-{mail.id}") == "error"
        mark_read.assert_awaited_once_with(mail)

    async def test_reply_failure_does_not_break_reap(
        self, journal: SessionJournal
    ) -> None:
        mail = _mail()
        loop_driver = MagicMock()
        loop_driver.send_failure_reply = AsyncMock(side_effect=OSError("bus down"))
        loop_driver.step_session = AsyncMock(side_effect=RuntimeError("boom"))
        scheduler, mark_read = _make_scheduler(journal, loop_driver=loop_driver)

        scheduler.enqueue(mail, {})
        scheduler.start()
        try:
            await self._wait_reaped(mark_read)
        finally:
            await scheduler.stop()

        # The reply failed, but the session still ended and was reaped.
        loop_driver.send_failure_reply.assert_awaited_once()
        assert await journal.end_reason(f"agent1-{mail.id}") == "error"
        mark_read.assert_awaited_once_with(mail)
