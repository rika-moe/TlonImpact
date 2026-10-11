"""Stage purity (C2): stages declare, the engine applies.

Stages are pure transformations ``Session x Services -> StageOutcome``:
no direct session mutation. These tests pin both halves — the stages
(think/act) only declare, and ``LoopDriver._apply_outcome`` applies the
declarations in the order the pre-C2 interleaving had.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from tlon.agents.context_pressure import ContextPressure
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.session import AgentSession, SessionState
from tlon.agents.session_journal import SessionJournal
from tlon.agents.stages.act import act_stage_collect
from tlon.agents.stages.outcome import StageOutcome
from tlon.agents.stages.think import think_stage
from tlon.agents.trigger import AgentIdentity
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.tools.base import ToolResult
from tlon.tools.spill import ToolSpill


def _make_services() -> MagicMock:
    svc = MagicMock()
    svc.logger = MagicMock()
    svc.publish_event = AsyncMock()
    return svc


def _make_driver(executor: MagicMock, tmp_path: Path) -> LoopDriver:
    rule = MagicMock()
    rule.loop.fuel = None
    journal = SessionJournal("agent1", TlonPaths(home=tmp_path))
    return LoopDriver(
        identity=AgentIdentity(id="agent1", name="Agent", address="agent1@tlon"),
        rule=rule,
        services=_make_services(),
        executor=executor,
        tool_executor=MagicMock(),
        per_tool_timeout=1.0,
        on_complete=MagicMock(),
        journal=journal,
        spill=ToolSpill(TlonPaths(home=tmp_path), "agent1"),
        # Default context window: the threshold is never crossed here.
        pressure=ContextPressure(MagicMock(), journal, 262144),
        is_human_sender=lambda address: True,
    )


class TestStagePurity:
    async def test_think_stage_declares_response_without_touching_session(self) -> None:
        svc = _make_services()
        response = LLMResponse(content="hi")
        svc.think = AsyncMock(return_value=response)
        session = AgentSession(session_id="s1", trigger_content="trigger")

        outcome = await think_stage(svc, session)

        assert outcome.response is response
        # Declared, not written: the session field is the engine's job.
        assert session.last_llm_response is None

    async def test_act_collect_declares_harvest_without_mutating_session(self) -> None:
        svc = _make_services()
        session = AgentSession(session_id="s1", trigger_content="trigger")
        session.mark_waiting()
        tc = ToolCall(id="t1", function_name="someTool", arguments={})
        result = ToolResult(success=True, data={"out": 1})

        outcome = await act_stage_collect(svc, session, [(tc, result)])

        assert outcome.tool_results == [(tc, result)]
        assert outcome.advance_iteration is True
        assert outcome.mark_active is True
        assert len(outcome.context_appends) == 2  # assistant + tool messages
        # Session untouched: everything above is only declared.
        assert session.all_tool_results == []
        assert session.iteration == 0
        assert session.state == SessionState.WAITING

    async def test_act_collect_completion_is_declared_not_applied(self) -> None:
        svc = _make_services()
        session = AgentSession(session_id="s1", trigger_content="trigger")
        session.set_last_llm_response(LLMResponse(content="working"))
        tc = ToolCall(id="t1", function_name="sendEmail", arguments={})
        result = ToolResult(success=True, data={"content": "done", "is_complete": True})

        outcome = await act_stage_collect(svc, session, [(tc, result)])

        assert outcome.complete is True
        assert outcome.run_on_complete is True
        assert outcome.final_reply == "done"
        assert len(outcome.mails) == 1
        assert session.is_completed is False

    async def test_act_collect_still_waiting_declares_nothing(self) -> None:
        svc = _make_services()
        session = AgentSession(session_id="s1", trigger_content="trigger")

        outcome = await act_stage_collect(svc, session, [])

        assert outcome == StageOutcome()

    async def test_act_collect_send_email_inherits_trigger_headers_by_default(
        self,
    ) -> None:
        """Without an explicit in_reply_to the reply keeps the status
        quo: reply headers come from the session's trigger mail."""
        svc = _make_services()
        session = AgentSession(session_id="s1", trigger_content="trigger")
        trigger = Mail.create(
            from_address="user@tlon.internal",
            to=["agent1@tlon.internal"],
            content="触发邮件",
        )
        session.trigger_message = trigger
        tc = ToolCall(id="t1", function_name="sendEmail", arguments={})
        result = ToolResult(
            success=True,
            data={"to": ["user@tlon.internal"], "content": "回复", "is_complete": True},
        )

        outcome = await act_stage_collect(svc, session, [(tc, result)])

        assert len(outcome.mails) == 1
        mail = outcome.mails[0]
        assert mail.to == ["user@tlon.internal"]
        assert mail.in_reply_to == trigger.id
        assert mail.thread_id == trigger.thread_id

    async def test_act_collect_send_email_explicit_in_reply_to_wins(self) -> None:
        """The model's explicit in_reply_to (reply to a non-trigger
        mail) is declared on the MailEffect. The stage cannot read the
        mailbox, so thread_id stays the trigger-derived fallback — the
        engine re-derives it from the referenced mail
        (docs/design/mail.md)."""
        svc = _make_services()
        session = AgentSession(session_id="s1", trigger_content="trigger")
        trigger = Mail.create(
            from_address="user@tlon.internal",
            to=["agent1@tlon.internal"],
            content="触发邮件",
        )
        session.trigger_message = trigger
        tc = ToolCall(id="t1", function_name="sendEmail", arguments={})
        result = ToolResult(
            success=True,
            data={
                "to": ["other@tlon.internal"],
                "content": "回复更早的那封",
                "is_complete": False,
                "in_reply_to": "m-earlier",
            },
        )

        outcome = await act_stage_collect(svc, session, [(tc, result)])

        assert len(outcome.mails) == 1
        mail = outcome.mails[0]
        assert mail.to == ["other@tlon.internal"]
        assert mail.in_reply_to == "m-earlier"
        assert mail.thread_id == trigger.thread_id  # fallback; engine re-derives


class TestEngineApplication:
    async def test_response_recorded_as_last_llm_response(self, tmp_path: Path) -> None:
        driver = _make_driver(MagicMock(), tmp_path)
        session = AgentSession(session_id="s1", trigger_content="trigger")
        response = LLMResponse(content="hi")

        chain = await driver._apply_outcome(session, None, StageOutcome(response=response))

        assert chain is response
        assert session.last_llm_response is response

    async def test_tool_results_and_final_reply_applied(self, tmp_path: Path) -> None:
        executor = MagicMock()
        on_complete = MagicMock()
        on_complete.run = AsyncMock()
        driver = _make_driver(executor, tmp_path)
        driver._on_complete = on_complete
        session = AgentSession(session_id="s1", trigger_content="trigger")
        tc = ToolCall(id="t1", function_name="someTool", arguments={})
        result = ToolResult(success=True, data={})
        outcome = StageOutcome(
            tool_results=[(tc, result)], final_reply="final", run_on_complete=True
        )

        await driver._apply_outcome(session, None, outcome)

        # The harvested results land on the session; the final reply is
        # the on-complete env's `result` (ADR 0009: no memory save).
        assert session.all_tool_results == [{"tool_call": tc, "result": result}]
        on_complete.run.assert_awaited_once_with(session, "final")

    async def test_mark_active_and_iteration_applied(self, tmp_path: Path) -> None:
        driver = _make_driver(MagicMock(), tmp_path)
        session = AgentSession(session_id="s1", trigger_content="trigger")
        session.mark_waiting()

        await driver._apply_outcome(
            session, None, StageOutcome(advance_iteration=True, mark_active=True)
        )

        assert session.state == SessionState.ACTIVE
        assert session.iteration == 1

    async def test_collect_session_results_harvests_in_engine(self, tmp_path: Path) -> None:
        """The harvest (collect_completed) is the engine's; the stage
        receives the completed results and its Outcome is applied."""
        executor = MagicMock()
        driver = _make_driver(executor, tmp_path)
        session = AgentSession(session_id="s1", trigger_content="trigger")
        session.mark_waiting()
        tc = ToolCall(id="t1", function_name="someTool", arguments={})
        result = ToolResult(success=True, data={})

        async def fake_task() -> tuple[ToolCall, ToolResult]:
            return tc, result

        task = asyncio.create_task(fake_task())
        await task
        session.dispatch_tools([tc], {"t1": task})

        await driver.collect_session_results(session)

        assert session.all_tool_results == [{"tool_call": tc, "result": result}]
        assert session.state == SessionState.ACTIVE
        assert session.iteration == 1

    async def test_act_collect_silence_completes_without_mail(self) -> None:
        """silence is a completion signal: loop ends, no mail, no final
        reply (there is no reply content to pass to on-complete)."""
        svc = _make_services()
        session = AgentSession(session_id="s1", trigger_content="trigger")
        session.set_last_llm_response(LLMResponse(content="working"))
        tc = ToolCall(id="t1", function_name="silence", arguments={"reason": "循环"})
        result = ToolResult(success=True, data={"silenced": True, "reason": "循环"})

        outcome = await act_stage_collect(svc, session, [(tc, result)])

        assert outcome.complete is True
        assert outcome.run_on_complete is True
        assert outcome.mails == []  # nothing sent
        assert outcome.final_reply is None  # nothing to pass on
        assert session.is_completed is False

    async def test_act_step_silence_conflicts_with_send_email(self) -> None:
        """A silence round must be the only tool call (like sendEmail
        is_complete=true): silence + sendEmail in the same round is
        refused with a corrective prompt."""
        from tlon.agents.stages.act import act_stage_step

        svc = _make_services()
        session = AgentSession(session_id="s1", trigger_content="trigger")
        response = LLMResponse(
            content="done",
            tool_calls=[
                ToolCall(id="s1", function_name="silence", arguments={}),
                ToolCall(id="m1", function_name="sendEmail", arguments={"is_complete": True}),
            ],
        )

        outcome = await act_stage_step(svc, session, response)

        assert outcome.tool_dispatches == []  # refused
        assert any("冲突" in str(a.get("content")) for a in outcome.context_appends)
        assert outcome.advance_iteration is True
