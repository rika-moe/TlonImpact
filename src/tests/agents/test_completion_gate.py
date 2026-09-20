"""Pre-submission critique gate (issue ): the lightweight default loop.

The default cognitive stage vector gates the completing act on a critique
of the pending draft:

    [(think)
     (critique {:if (completing?)})
     (act {:if (or (not (completing?)) (not (critique-failed?)))})
     (revise {:if (critique-failed?)})]

``completing?`` is true exactly when the round's pending response would
end the session (a sole ``sendEmail is_complete=true`` or ``silence``
call). critique therefore costs one extra LLM call per completion
*attempt* — independent of the round count — and a FAIL blocks the
dispatch, feeds the feedback back via revise (which clears the consumed
verdict), and lets the next think revise the draft. An unparseable
verdict writes no verdict (), so the gate stays open (fail-open,
matching critique's soft-failure semantics).
"""

import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from tlon.agents.context_builder import BuiltContext
from tlon.agents.context_pressure import ContextPressure
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.member_spec import MemberSpec, validate_member_spec
from tlon.agents.session import AgentSession, SessionState
from tlon.agents.session_journal import SessionJournal
from tlon.agents.stages.act import attempts_completion
from tlon.agents.trigger import AgentIdentity
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.tools.base import ToolResult
from tlon.tools.spill import ToolSpill
from tlosure.builtins import STAGE_COND_BUILTINS
from tlosure.edn import loads

GATED_EDN = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :loop {:context {:sections [(persona) (protocol)]}
                 :stages [(think)
                          (critique {:if (completing?)})
                          (act {:if (or (not (completing?)) (not (critique-failed?)))})
                          (revise {:if (critique-failed?)})]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :reflect {:enabled false :interval-minutes 5}}}]}
"""


def _mail() -> Mail:
    return Mail.create(
        from_address="user@tlon.internal", content="hi", to=["agent1@tlon.internal"]
    )


def _tc(tc_id: str, name: str = "someTool", arguments: dict | None = None) -> ToolCall:
    return ToolCall(id=tc_id, function_name=name, arguments=arguments or {})


def _completing(content: str, tc_id: str = "c-final") -> LLMResponse:
    return LLMResponse(
        content="done",
        tool_calls=[_tc(tc_id, "sendEmail", {"content": content, "is_complete": True})],
    )


async def _send_tool(name: str, args: dict) -> ToolResult:
    if name == "sendEmail":
        return ToolResult(
            success=True,
            data={"content": args["content"], "is_complete": args.get("is_complete")},
        )
    return ToolResult(success=True, data={"ok": name})


def _make_driver(
    tmp_path: Path,
    responses: list[LLMResponse],
) -> tuple[LoopDriver, SessionJournal, MagicMock, MagicMock]:
    spec = MemberSpec.from_edn(loads(GATED_EDN))
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
    executor.send_mail = AsyncMock()
    executor.publish = AsyncMock()
    executor.mark_mail_read = AsyncMock()

    tool_executor = MagicMock()
    tool_executor.execute = AsyncMock(side_effect=_send_tool)

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


def _calls_by_span(services: MagicMock, span: str) -> list:
    return [c for c in services.think.await_args_list if c.kwargs.get("span") == span]


class TestAttemptsCompletion:
    """The predicate the engine exposes to stage conditions as
    ``round.completing``; must match act's completion/conflict rule."""

    def test_none_and_tool_less_responses_do_not_complete(self) -> None:
        assert attempts_completion(None) is False
        assert attempts_completion(LLMResponse(content="thinking")) is False

    def test_send_email_is_complete_completes(self) -> None:
        assert attempts_completion(_completing("reply")) is True

    def test_silence_alone_completes(self) -> None:
        response = LLMResponse(content=None, tool_calls=[_tc("c1", "silence")])
        assert attempts_completion(response) is True

    def test_progress_mail_does_not_complete(self) -> None:
        response = LLMResponse(
            content=None,
            tool_calls=[_tc("c1", "sendEmail", {"content": "wip", "is_complete": False})],
        )
        assert attempts_completion(response) is False

    def test_conflicting_completion_does_not_complete(self) -> None:
        # A completion signal mixed with other tools is act's conflict
        # case: nothing is dispatched, the round does not complete.
        response = LLMResponse(
            content=None,
            tool_calls=[
                _tc("c1", "sendEmail", {"content": "reply", "is_complete": True}),
                _tc("c2", "someTool"),
            ],
        )
        assert attempts_completion(response) is False

    def test_builtin_reads_round_section_none_safe(self) -> None:
        completing = STAGE_COND_BUILTINS["completing?"].fn
        assert completing({"round": {"completing": True}}) is True
        assert completing({"round": {"completing": False}}) is False
        assert completing({"round": {}}) is False
        assert completing({}) is False
        assert completing({"round": None}) is False


class TestCompletionGate:
    async def test_passed_critique_lets_the_reply_through(self, tmp_path: Path) -> None:
        mail = _mail()
        driver, _, executor, services = _make_driver(
            tmp_path, [_completing("reply"), LLMResponse(content="PASS")]
        )
        session = AgentSession(
            session_id=f"agent1-{mail.id}", trigger_content=mail.content
        )

        await _drive(driver, session, mail)

        assert session.is_completed
        executor.send_mail.assert_awaited_once()
        assert executor.send_mail.await_args.args[0].content == "reply"
        # think + one critique: the reviewer saw the pending draft.
        assert len(_calls_by_span(services, "agent.critique")) == 1
        critique_input = _calls_by_span(services, "agent.critique")[0].args[0]
        assert any("reply" in str(m.get("content")) for m in critique_input)

    async def test_failed_critique_blocks_act_and_feeds_back(self, tmp_path: Path) -> None:
        mail = _mail()
        driver, journal, executor, services = _make_driver(
            tmp_path,
            [
                _completing("draft-1"),
                LLMResponse(content="FAIL\n没有回答来信的问题"),
                _completing("draft-2"),
                LLMResponse(content="PASS"),
            ],
        )
        session = AgentSession(
            session_id=f"agent1-{mail.id}", trigger_content=mail.content
        )

        # Round 1: the FAILed draft must never reach act's dispatch.
        await driver.step_session(session, mail)
        assert session.state == SessionState.ACTIVE  # not WAITING: nothing dispatched
        executor.send_mail.assert_not_awaited()
        # revise fed the feedback into context and consumed the verdict.
        assert any(
            "没有回答来信的问题" in str(m.get("content")) for m in session.context
        )
        assert session.loop_scratch.get("critique") is None

        # Round 2: the revised draft is reviewed again and passes.
        await _drive(driver, session, mail)

        assert session.is_completed
        executor.send_mail.assert_awaited_once()
        assert executor.send_mail.await_args.args[0].content == "draft-2"
        # The revising think saw the feedback.
        second_think = _calls_by_span(services, "agent.think")[1].args[0]
        assert any("没有回答来信的问题" in str(m.get("content")) for m in second_think)
        # Journal and memory agree on the model-visible context.
        assert await journal.derive_context(session.id) == session.context

    async def test_working_rounds_pay_no_critique(self, tmp_path: Path) -> None:
        mail = _mail()
        working = LLMResponse(
            content="working", tool_calls=[_tc("c1", "someTool", {"a": 1})]
        )
        driver, _, executor, services = _make_driver(
            tmp_path, [working, _completing("final"), LLMResponse(content="PASS")]
        )
        session = AgentSession(
            session_id=f"agent1-{mail.id}", trigger_content=mail.content
        )

        await _drive(driver, session, mail)

        assert session.is_completed
        # Two thinks + exactly one critique (the completing round only).
        assert len(_calls_by_span(services, "agent.think")) == 2
        assert len(_calls_by_span(services, "agent.critique")) == 1

    async def test_conflicting_completion_skips_critique(self, tmp_path: Path) -> None:
        mail = _mail()
        conflict = LLMResponse(
            content=None,
            tool_calls=[
                _tc("c1", "sendEmail", {"content": "reply", "is_complete": True}),
                _tc("c2", "someTool"),
            ],
        )
        driver, _, _, services = _make_driver(
            tmp_path, [conflict, _completing("final"), LLMResponse(content="PASS")]
        )
        session = AgentSession(
            session_id=f"agent1-{mail.id}", trigger_content=mail.content
        )

        await _drive(driver, session, mail)

        assert session.is_completed
        # The conflict round is not completing: act's conflict reminder
        # fired and no critique call was spent on it.
        assert any("冲突" in str(m.get("content")) for m in session.context)
        assert len(_calls_by_span(services, "agent.critique")) == 1

    async def test_unparseable_verdict_leaves_the_gate_open(self, tmp_path: Path) -> None:
        """ semantics under the gate: an unknown review outcome is a
        skipped review, not a block — the reply still goes out."""
        mail = _mail()
        driver, _, executor, _ = _make_driver(
            tmp_path, [_completing("reply"), LLMResponse(content="我觉得还行")]
        )
        session = AgentSession(
            session_id=f"agent1-{mail.id}", trigger_content=mail.content
        )

        await _drive(driver, session, mail)

        assert session.is_completed
        executor.send_mail.assert_awaited_once()
        assert executor.send_mail.await_args.args[0].content == "reply"
        assert session.loop_scratch.get("critique") is None

    async def test_silence_decision_is_reviewed(self, tmp_path: Path) -> None:
        """silence is also a completion signal: it goes through the gate."""
        mail = _mail()
        silent = LLMResponse(content=None, tool_calls=[_tc("c1", "silence")])
        driver, _, executor, services = _make_driver(
            tmp_path, [silent, LLMResponse(content="FAIL\n不应沉默，应回复确认收到")]
        )
        session = AgentSession(
            session_id=f"agent1-{mail.id}", trigger_content=mail.content
        )

        await driver.step_session(session, mail)

        # The silence call was blocked; the feedback is queued for the
        # next think instead.
        executor.send_mail.assert_not_awaited()
        assert session.state == SessionState.ACTIVE
        assert len(_calls_by_span(services, "agent.critique")) == 1
        assert any("不应沉默" in str(m.get("content")) for m in session.context)
