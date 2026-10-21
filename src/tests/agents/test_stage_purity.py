"""Stage purity (C2): stages declare, the engine applies.

Stages are pure transformations ``Session x Services -> StageOutcome``:
no direct session mutation. These tests pin both halves — the stages
(think/act) only declare, and ``LoopDriver._apply_outcome`` applies the
declarations in the order the pre-C2 interleaving had.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.agents.context_pressure import ContextPressure
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.member_spec import ReflectSpec, SessionRecipe
from tlon.agents.session import AgentSession, SessionState
from tlon.agents.session_journal import SessionJournal
from tlon.agents.stages.act import act_stage_collect, act_stage_step
from tlon.agents.stages.outcome import StageOutcome
from tlon.agents.stages.think import think_stage
from tlon.agents.trigger import AgentIdentity
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.base.paths import TlonPaths
from tlon.operations.control import control_names
from tlon.tools.base import ToolResult
from tlon.tools.spill import ToolSpill
from tlosure.edn import Form


def _make_services() -> MagicMock:
    svc = MagicMock()
    svc.logger = MagicMock()
    svc.control_names.return_value = control_names()
    svc.publish_event = AsyncMock()
    return svc


def _make_driver(executor: MagicMock, tmp_path: Path) -> LoopDriver:
    recipe = SessionRecipe((), (), Form(), 10, 3, 1000000, ReflectSpec(False, 0))
    journal = SessionJournal("agent1", TlonPaths(home=tmp_path))
    return LoopDriver(
        identity=AgentIdentity(id="agent1", name="Agent", address="agent1@tlon"),
        recipe=recipe,
        services=_make_services(),
        executor=executor,
        tool_executor=MagicMock(),
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
        tc = ToolCall(id="t1", function_name="finish-turn", arguments={})
        result = ToolResult(
            success=True, data={"scheduling_request": {"action": "finish", "reason": ""}}
        )

        outcome = await act_stage_collect(svc, session, [(tc, result)])

        assert outcome.complete is True
        assert outcome.scratch_updates["scheduling_request"]["action"] == "finish"
        assert session.is_completed is False

    async def test_act_collect_still_waiting_declares_nothing(self) -> None:
        svc = _make_services()
        session = AgentSession(session_id="s1", trigger_content="trigger")

        outcome = await act_stage_collect(svc, session, [])

        assert outcome == StageOutcome()

    async def test_act_collect_send_email_declares_model_headers_verbatim(
        self,
    ) -> None:
        """ADR 0028: no trigger mail — ``to`` / ``in_reply_to`` are exactly
        what the model declared; thread_id stays None (the engine
        re-derives it from the referenced mail, docs/design/mail.md)."""
        svc = _make_services()
        session = AgentSession(session_id="s1", trigger_content="trigger")
        tc = ToolCall(id="t1", function_name="mail-send", arguments={})
        result = ToolResult(
            success=True,
            data={"to": ["user@tlon.internal"], "content": "回复"},
        )

        outcome = await act_stage_collect(svc, session, [(tc, result)])

        assert outcome.tool_results == [(tc, result)]
        mail = result.data
        assert mail.get("to") == ["user@tlon.internal"]
        assert mail.get("in_reply_to") is None
        assert mail.get("thread_id") is None

    async def test_act_collect_send_email_explicit_in_reply_to_passes_through(self) -> None:
        """Collection preserves the result headers already submitted by the tool."""
        svc = _make_services()
        session = AgentSession(session_id="s1", trigger_content="trigger")
        tc = ToolCall(id="t1", function_name="mail-send", arguments={})
        result = ToolResult(
            success=True,
            data={
                "to": ["other@tlon.internal"],
                "content": "回复更早的那封",
                "in_reply_to": "m-earlier",
            },
        )

        outcome = await act_stage_collect(svc, session, [(tc, result)])

        assert outcome.tool_results == [(tc, result)]
        mail = result.data
        assert mail.get("to") == ["other@tlon.internal"]
        assert mail.get("in_reply_to") == "m-earlier"
        assert mail.get("thread_id") is None

    async def test_act_collect_forward_mail_declares_effect_without_completing(self) -> None:
        """A submitted forward result retains its source and comment;
        collecting it never completes the loop."""
        svc = _make_services()
        session = AgentSession(session_id="s1", trigger_content="trigger")
        session.mark_waiting()
        tc = ToolCall(id="t1", function_name="mail-forward", arguments={})
        result = ToolResult(
            success=True,
            data={
                "mail_id": "m-source",
                "to": ["other@tlon.internal"],
                "cc": [],
                "comment": "转给你",
                "action": "forward_mail",
            },
        )

        outcome = await act_stage_collect(svc, session, [(tc, result)])

        assert outcome.tool_results == [(tc, result)]
        mail = result.data
        assert mail.get("to") == ["other@tlon.internal"]
        assert mail.get("content", "") == ""
        assert mail.get("mail_id") == "m-source"
        assert mail.get("comment") == "转给你"
        assert outcome.complete is False
        assert outcome.advance_iteration is True
        assert outcome.mark_active is True


class TestEngineApplication:
    async def test_response_recorded_as_last_llm_response(self, tmp_path: Path) -> None:
        driver = _make_driver(MagicMock(), tmp_path)
        session = AgentSession(session_id="s1", trigger_content="trigger")
        response = LLMResponse(content="hi")

        chain = await driver._apply_outcome(session, None, StageOutcome(response=response))

        assert chain is response
        assert session.last_llm_response is response

    async def test_tool_results_applied(self, tmp_path: Path) -> None:
        driver = _make_driver(MagicMock(), tmp_path)
        session = AgentSession(session_id="s1", trigger_content="trigger")
        tc = ToolCall(id="t1", function_name="someTool", arguments={})
        result = ToolResult(success=True, data={})
        await driver._apply_outcome(session, None, StageOutcome(tool_results=[(tc, result)]))
        assert session.all_tool_results == [{"tool_call": tc, "result": result}]

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

    async def test_act_collect_finish_turn_completes_without_mail(self) -> None:
        """finish-turn is a completion signal: loop ends, no mail, no final
        reply."""
        svc = _make_services()
        session = AgentSession(session_id="s1", trigger_content="trigger")
        session.set_last_llm_response(LLMResponse(content="working"))
        tc = ToolCall(id="t1", function_name="finish-turn", arguments={})
        result = ToolResult(
            success=True, data={"scheduling_request": {"action": "finish", "reason": ""}}
        )

        outcome = await act_stage_collect(svc, session, [(tc, result)])

        assert outcome.complete is True
        assert session.is_completed is False

    async def test_act_step_finish_turn_conflicts_with_send_email(self) -> None:
        """A finish-turn round must be the only tool call:
        finish-turn + mail-send in the same round is
        refused with a corrective prompt."""
        svc = _make_services()
        session = AgentSession(session_id="s1", trigger_content="trigger")
        response = LLMResponse(
            content="done",
            tool_calls=[
                ToolCall(id="s1", function_name="finish-turn", arguments={}),
                ToolCall(id="m1", function_name="mail-send", arguments={}),
            ],
        )

        outcome = await act_stage_step(svc, session, response)

        assert outcome.tool_dispatches == []  # refused
        assert any("冲突" in str(a.get("content")) for a in outcome.context_appends)
        assert outcome.advance_iteration is True


@pytest.mark.parametrize("ending", ["finish-turn", "sleep-until", "exit-agent"])
async def test_scheduling_request_cannot_share_a_round(ending: str) -> None:
    svc = _make_services()
    session = AgentSession(session_id="s-mixed", trigger_content="work")
    response = LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(id="end", function_name=ending, arguments={}),
            ToolCall(id="mail", function_name="mail-send", arguments={"content": "hi"}),
        ],
    )
    outcome = await act_stage_step(svc, session, response)
    assert not outcome.tool_dispatches
    assert not outcome.complete
    assert outcome.advance_iteration


async def test_failed_sleep_does_not_end_work() -> None:
    from tlon.lifecycle.service import LifecycleService
    from tlon.operations.adapters import OperationTool
    from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
    from tlon.operations.lifecycle import make_lifecycle_operations

    svc = _make_services()
    session = AgentSession(session_id="s-invalid-sleep", trigger_content="work")
    call = ToolCall(id="sleep", function_name="sleep-until", arguments={"wake_at": "yesterday"})
    executor = OperationExecutor(
        OperationRegistry(make_lifecycle_operations(LifecycleService("agent1"))),
        CallContext("agent1", "s", "call"),
    )
    result = await OperationTool(executor, "sleep-until").execute_context(call.arguments)
    outcome = await act_stage_collect(svc, session, [(call, result)])
    assert not outcome.complete
    assert not outcome.scratch_updates
    assert outcome.mark_active


@pytest.mark.parametrize("second_name", ["state-put", "mail-send"])
async def test_duplicate_call_ids_reject_whole_round_without_dispatch(
    tmp_path: Path, second_name: str
) -> None:
    calls = [
        ToolCall(id="same", function_name="state-put", arguments={"key": "a", "value": 1}),
        ToolCall(id="same", function_name=second_name, arguments={"key": "b", "value": 2}),
    ]
    response = LLMResponse(content=None, tool_calls=calls)
    session = AgentSession(session_id="duplicate-ids", trigger_content="trigger")
    svc = _make_services()
    outcome = await act_stage_step(svc, session, response)
    assert outcome.tool_dispatches == []
    assert outcome.advance_iteration
    assert "没有执行任何工具" in outcome.context_appends[0]["content"]
    executor = MagicMock()
    driver = _make_driver(executor, tmp_path)
    driver._execute_declared_tool = AsyncMock()
    # Even a custom stage cannot bypass the engine's identity precondition.
    with pytest.raises(ValueError, match="Duplicate ToolCall.id"):
        await driver._apply_outcome(session, response, StageOutcome(tool_dispatches=calls))
    driver._execute_declared_tool.assert_not_awaited()
    assert not session.has_pending_tools
    assert await driver._journal.read_events(session.id) == []


async def test_control_semantics_follow_definition_not_operation_name() -> None:
    from tlon.operations.control import validate_control_forms
    from tlon.operations.core import Operation
    from tlon.operations.schema import InputError
    from tlosure.edn import loads

    async def stop(_context: object, _arguments: dict[str, object]) -> dict[str, object]:
        return {"scheduling_request": {"action": "finish"}}

    control = Operation(
        "custom-halt",
        "host control",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        True,
        stop,
        effects=frozenset({"write"}),
        control=True,
    )
    names = control_names([control])
    validate_control_forms([loads("(custom-halt {})")], controls=names)
    with pytest.raises(InputError, match="sole direct"):
        validate_control_forms([loads("(let [x true] (custom-halt {}))")], controls=names)
    svc = _make_services()
    svc.control_names.return_value = names
    session = AgentSession(session_id="custom-control", trigger_content="trigger")
    response = LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(id="control", function_name="custom-halt", arguments={}),
            ToolCall(id="read", function_name="state-get", arguments={"key": "x"}),
        ],
    )
    outcome = await act_stage_step(svc, session, response)
    assert not outcome.tool_dispatches
    assert "冲突" in outcome.context_appends[0]["content"]
    outcome = await act_stage_collect(
        svc,
        session,
        [
            (
                response.tool_calls[0],
                ToolResult(success=True, data={"scheduling_request": {"action": "finish"}}),
            )
        ],
    )
    assert outcome.complete
