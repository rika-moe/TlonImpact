"""Stage purity (C2): stages declare, the engine applies.

Stages are pure transformations ``Session x Services -> StageOutcome``:
no direct session mutation. These tests pin both halves — the stages
(think/act) only declare, and ``LoopDriver._apply_outcome`` applies the
declarations in the order the pre-C2 interleaving had (tool results
land before the interaction save).
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from tlon.agents.loop_driver import LoopDriver
from tlon.agents.session import AgentSession, SessionState
from tlon.agents.stages.act import act_stage_collect
from tlon.agents.stages.outcome import StageOutcome
from tlon.agents.stages.think import think_stage
from tlon.agents.trigger import AgentIdentity
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.tools.base import ToolResult


def _make_services() -> MagicMock:
    svc = MagicMock()
    svc.logger = MagicMock()
    svc.publish_event = AsyncMock()
    return svc


def _make_driver(executor: MagicMock) -> LoopDriver:
    rule = MagicMock()
    rule.loop.fuel = None
    return LoopDriver(
        identity=AgentIdentity(id="agent1", name="Agent", address="agent1@tlon"),
        rule=rule,
        services=_make_services(),
        executor=executor,
        tool_executor=MagicMock(),
        per_tool_timeout=1.0,
        on_complete=MagicMock(),
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
        assert outcome.save_interaction == "done"
        assert len(outcome.mails) == 1
        assert session.is_completed is False

    async def test_act_collect_still_waiting_declares_nothing(self) -> None:
        svc = _make_services()
        session = AgentSession(session_id="s1", trigger_content="trigger")

        outcome = await act_stage_collect(svc, session, [])

        assert outcome == StageOutcome()


class TestEngineApplication:
    async def test_response_recorded_as_last_llm_response(self) -> None:
        driver = _make_driver(MagicMock())
        session = AgentSession(session_id="s1", trigger_content="trigger")
        response = LLMResponse(content="hi")

        chain = await driver._apply_outcome(session, None, StageOutcome(response=response))

        assert chain is response
        assert session.last_llm_response is response

    async def test_tool_results_land_before_save_interaction(self) -> None:
        executor = MagicMock()
        executor.save_interaction = AsyncMock()
        driver = _make_driver(executor)
        session = AgentSession(session_id="s1", trigger_content="trigger")
        tc = ToolCall(id="t1", function_name="someTool", arguments={})
        result = ToolResult(success=True, data={})
        outcome = StageOutcome(tool_results=[(tc, result)], save_interaction="final")

        await driver._apply_outcome(session, None, outcome)

        # The save sees this round's results (pre-C2 ordering preserved).
        assert session.all_tool_results == [{"tool_call": tc, "result": result}]
        executor.save_interaction.assert_awaited_once_with(
            "final", session.last_llm_response, session.all_tool_results
        )

    async def test_mark_active_and_iteration_applied(self) -> None:
        driver = _make_driver(MagicMock())
        session = AgentSession(session_id="s1", trigger_content="trigger")
        session.mark_waiting()

        await driver._apply_outcome(
            session, None, StageOutcome(advance_iteration=True, mark_active=True)
        )

        assert session.state == SessionState.ACTIVE
        assert session.iteration == 1

    async def test_collect_session_results_harvests_in_engine(self) -> None:
        """The harvest (collect_completed) is the engine's; the stage
        receives the completed results and its Outcome is applied."""
        executor = MagicMock()
        driver = _make_driver(executor)
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
