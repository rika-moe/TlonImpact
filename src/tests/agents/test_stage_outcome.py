"""C1/C2: StageOutcome — stage unit tests + engine-side application.

The pure stages declare state changes as data (StageOutcome); the
LoopDriver applies them. These tests pin both sides: the stages'
outcomes (direct stage calls) and the interpreter's application
mechanics (_apply_outcome units + step_session integration), plus the
C2 decoupling proof: the LoopDriver runs a full think -> act ->
complete round on injected capabilities alone, with no Agent.
"""

import asyncio
import dataclasses
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from tlon.agents.edn import loads
from tlon.agents.executor import EffectExecutor
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.session import AgentSession, SessionState
from tlon.agents.spec import AgentSpec, standard_behavior_edn
from tlon.agents.stages.act import act_stage_collect, act_stage_step
from tlon.agents.stages.outcome import MailEffect, StageOutcome
from tlon.agents.stages.services import StageServices
from tlon.agents.stages.think import think_stage
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.trigger import AgentIdentity
from tlon.base.events.event import EventType
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.organization.mail.mail import Mail
from tlon.tools.base import ToolResult


def _standard_spec() -> AgentSpec:
    """The canonical complete spec (standard ReAct program)."""
    return AgentSpec.from_edn(loads(standard_behavior_edn()))


def _make_spec(loop_edn: str) -> AgentSpec:
    """Build a complete AgentSpec, overriding :loop keys with a partial EDN map."""
    overrides: dict[str, Any] = {}
    for key, value in loads(loop_edn).items():
        name = str(key).replace("-", "_")
        # Form is a list subclass: only plain vectors (:stages) become tuples
        overrides[name] = tuple(value) if type(value) is list else value
    base = _standard_spec()
    return dataclasses.replace(base, loop=dataclasses.replace(base.loop, **overrides))


def _make_services(llm, *, logger=None, publish=None) -> StageServices:
    """StageServices over a mock LLM; context builder and publish are mocks."""
    context_builder = MagicMock()
    context_builder.get_system_prompt = AsyncMock(return_value="system prompt")
    context_builder.build_context = AsyncMock(
        return_value=[{"role": "user", "content": "邮件"}]
    )
    return StageServices(
        identity=AgentIdentity(id="a-001", name="TestAgent", address="a-001@tlon.internal"),
        spec=_standard_spec(),
        llm_client=llm,
        context_builder=context_builder,
        publish=publish if publish is not None else AsyncMock(),
        tool_definitions=lambda: [],
        logger=logger if logger is not None else MagicMock(),
    )


def _make_executor(*, publish=None, memory=None, reflection=None, mailbox=None) -> EffectExecutor:
    """An EffectExecutor over mocks (no Agent)."""
    return EffectExecutor(
        identity=AgentIdentity(id="a-001", name="TestAgent", address="a-001@tlon.internal"),
        spec=_standard_spec(),
        memory=memory if memory is not None else AsyncMock(),
        reflection=reflection if reflection is not None else MagicMock(),
        mailbox=mailbox,
        publish=publish if publish is not None else AsyncMock(),
        logger=MagicMock(),
    )


def _make_driver(spec: AgentSpec, llm) -> LoopDriver:
    """A LoopDriver on injected capabilities alone (C2 — no host/Agent)."""
    return LoopDriver(
        identity=AgentIdentity(id="a-001", name="TestAgent", address="a-001@tlon.internal"),
        spec=spec,
        services=_make_services(llm),
        executor=_make_executor(),
        tool_executor=ToolExecutor(),
        per_tool_timeout=30.0,
        on_complete=MagicMock(run=AsyncMock()),
    )


def _make_session() -> AgentSession:
    """A session with a preset context (skips context building)."""
    session = AgentSession("s-c1", "trigger")
    session.context = [{"role": "user", "content": "邮件"}]
    return session


def _make_message(content: str = "Hello") -> Mail:
    return Mail.create(
        from_address="user-001@tlon.internal",
        to=["a-001@tlon.internal"],
        content=content,
    )


class TestThinkStage:
    async def test_think_returns_response_in_outcome(self):
        """think: one LLM call; the response rides the Outcome.

        The session's last-response slot is set inside the stage — the
        session is the stage's own input state, not the host.
        """
        llm = MagicMock()
        llm.chat = AsyncMock(return_value=LLMResponse(content="答案", finish_reason="stop"))
        svc = _make_services(llm)
        session = _make_session()

        with patch.object(svc, "traced_span", wraps=svc.traced_span) as span:
            outcome = await think_stage(svc, session, None)

        assert outcome.response is not None
        assert outcome.response.content == "答案"
        assert session.last_llm_response is outcome.response
        assert outcome.context_appends == []
        assert outcome.scratch_updates == {}
        assert outcome.record_error is False
        span.assert_any_call("agent.think")

    async def test_think_failure_declares_error_and_advance(self):
        """LLM failure: error logged; record_error + advance_iteration are
        declared in the Outcome, not applied to the session."""
        logger = MagicMock()
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=Exception("boom"))
        svc = _make_services(llm, logger=logger)
        session = _make_session()

        outcome = await think_stage(svc, session, None)

        assert outcome.response is None
        assert outcome.record_error is True
        assert outcome.advance_iteration is True
        # Application is the engine's job: the session is untouched
        assert session.error_count == 0
        assert session.iteration == 0
        error_events = [c.args[0] for c in logger.error.call_args_list]
        assert "session_llm_error" in error_events


class TestApplyOutcome:
    """The engine applies what a pure stage declares."""

    async def test_applies_all_declared_changes(self):
        driver = _make_driver(_standard_spec(), MagicMock())
        session = _make_session()
        resp = LLMResponse(content="new")
        outcome = StageOutcome(
            response=resp,
            context_appends=[{"role": "user", "content": "x"}],
            scratch_updates={"k": 1},
            advance_iteration=True,
            record_error=True,
        )

        chained = await driver._apply_outcome(session, None, outcome)

        assert session.context[-1] == {"role": "user", "content": "x"}
        assert session.loop_scratch == {"k": 1}
        assert session.iteration == 1
        assert session.error_count == 1
        assert chained is resp

    async def test_default_outcome_is_noop_and_keeps_chain(self):
        driver = _make_driver(_standard_spec(), MagicMock())
        session = _make_session()
        before = list(session.context)
        sentinel = LLMResponse(content="chained")

        chained = await driver._apply_outcome(session, sentinel, StageOutcome())

        assert chained is sentinel  # None never clobbers the response chain
        assert session.context == before
        assert session.loop_scratch == {}
        assert session.iteration == 0
        assert session.error_count == 0

    async def test_completion_effects_applied_in_order(self):
        """Collect-path order: context -> mails -> save -> on_complete -> complete."""
        driver = _make_driver(_standard_spec(), MagicMock())
        session = _make_session()
        session.set_last_llm_response(LLMResponse(content="x"))

        order: list[str] = []
        driver._executor.send_mail = AsyncMock(side_effect=lambda effect: order.append("mails"))
        driver._executor.save_interaction = AsyncMock(
            side_effect=lambda *args: order.append("save")
        )
        driver._on_complete.run = AsyncMock(side_effect=lambda *args: order.append("on_complete"))
        session.mark_completed = lambda: order.append("complete")  # type: ignore[method-assign]

        tool_msg = {"role": "tool", "tool_call_id": "tc-1", "content": "{}"}
        outcome = StageOutcome(
            context_appends=[tool_msg],
            mails=[MailEffect(to=["u-1@tlon.internal"], content="Done!")],
            save_interaction="Done!",
            run_on_complete=True,
            complete=True,
        )

        await driver._apply_outcome(session, None, outcome)

        assert session.context[-1] is tool_msg  # context lands first
        assert order == ["mails", "save", "on_complete", "complete"]
        driver._executor.save_interaction.assert_awaited_once_with(
            "Done!", session.last_llm_response, session.all_tool_results
        )
        driver._on_complete.run.assert_awaited_once_with(session, "Done!")


class TestInterpreterOutcomePath:
    """step_session integration: outcomes land on the session via the engine."""

    async def test_think_failure_applied_and_round_stops(self):
        """stages [(think) (act)], LLM broken: the engine applies
        record_error + advance_iteration and ends the round before act."""
        spec = _make_spec("{:stages [(think) (act)]}")
        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=Exception("boom"))
        driver = _make_driver(spec, llm)
        session = _make_session()

        await driver.step_session(session, _make_message())

        assert session.error_count == 1
        assert session.iteration == 1
        assert session.state == SessionState.ACTIVE  # act never dispatched

    async def test_plan_outcome_applied_by_engine(self):
        """stages [(plan)]: scratch/context land via the engine; the
        act-less round advance still fires."""
        spec = _make_spec("{:stages [(plan {:max-steps 3})]}")
        llm = MagicMock()
        llm.chat = AsyncMock(return_value=LLMResponse(content="1. step"))
        driver = _make_driver(spec, llm)
        session = _make_session()

        await driver.step_session(session, _make_message())

        assert session.loop_scratch["plan"] == "1. step"
        assert session.context[-1] == {"role": "user", "content": "[执行计划]\n\n1. step"}
        assert session.iteration == 1

    async def test_revise_outcome_applied_by_engine(self):
        """stages [(revise)] with critique feedback in scratch: the
        feedback message lands in the context via the engine."""
        spec = _make_spec("{:stages [(revise)]}")
        driver = _make_driver(spec, MagicMock())
        session = _make_session()
        session.loop_scratch["critique"] = {"passed": False, "feedback": "补充引用"}

        await driver.step_session(session, _make_message())

        assert session.context[-1] == {
            "role": "user",
            "content": "[评审反馈]\n\n补充引用\n请据此修正。",
        }


# ── C2: act stage purification + the broken Agent <-> LoopDriver cycle ──


async def _finish(pair: tuple[ToolCall, ToolResult]) -> tuple[ToolCall, ToolResult]:
    """An already-resolved tool task body."""
    return pair


async def _dispatch_finished(
    session: AgentSession, tc: ToolCall, result: ToolResult
) -> None:
    """Record one finished background tool task on the session."""
    task = asyncio.create_task(_finish((tc, result)))
    await task
    session.dispatch_tools([tc], {tc.id: task})
    session.mark_waiting()


class TestActStepStage:
    """act step half: declares reminder / conflict / dispatch as Outcome data."""

    async def test_no_response_is_noop(self):
        logger = MagicMock()
        svc = _make_services(MagicMock(), logger=logger)
        session = _make_session()

        outcome = await act_stage_step(svc, session, None)

        assert outcome == StageOutcome()
        debug_events = [c.args[0] for c in logger.debug.call_args_list]
        assert "loop_stage_skipped" in debug_events

    async def test_no_tool_calls_declares_reminder(self):
        svc = _make_services(MagicMock())
        session = _make_session()
        before = list(session.context)
        response = LLMResponse(content="闲聊", reasoning_content="因为", finish_reason="stop")

        outcome = await act_stage_step(svc, session, response)

        assert outcome.advance_iteration is True
        assert outcome.tool_dispatches == []
        assert outcome.context_appends[0] == {
            "role": "assistant",
            "content": "闲聊",
            "reasoning_content": "因为",
        }
        assert outcome.context_appends[1]["role"] == "user"
        assert "禁止直接输出文本" in outcome.context_appends[1]["content"]
        # The stage declares; the engine applies — the session is untouched
        assert session.context == before
        assert session.iteration == 0

    async def test_no_tool_calls_without_content_declares_bare_reminder(self):
        svc = _make_services(MagicMock())
        response = LLMResponse(content=None, finish_reason="stop")

        outcome = await act_stage_step(svc, _make_session(), response)

        assert outcome.advance_iteration is True
        assert len(outcome.context_appends) == 1
        assert outcome.context_appends[0]["role"] == "user"
        assert "你必须调用至少一个工具" in outcome.context_appends[0]["content"]

    async def test_conflict_declared(self):
        svc = _make_services(MagicMock())
        response = LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(id="tc-1", function_name="readFile", arguments={"path": "/tmp/x"}),
                ToolCall(
                    id="tc-2",
                    function_name="sendEmail",
                    arguments={"content": "Done!", "is_complete": True},
                ),
            ],
        )

        outcome = await act_stage_step(svc, _make_session(), response)

        assert outcome.context_appends == [
            {
                "role": "user",
                "content": (
                    "冲突：当 sendEmail 的 is_complete 设为 true 时，"
                    "不得与其他工具同时调用。\n"
                    "请重新规划。"
                ),
            }
        ]
        assert outcome.advance_iteration is True
        assert outcome.tool_dispatches == []

    async def test_dispatch_declared_engine_owns_concurrency(self):
        """The stage only declares what to dispatch — no tasks, no WAITING."""
        svc = _make_services(MagicMock())
        session = _make_session()
        response = LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(id="tc-1", function_name="searchMemories", arguments={"query": "q"}),
                ToolCall(
                    id="tc-2",
                    function_name="sendEmail",
                    arguments={"content": "进度", "is_complete": False},
                ),
            ],
        )

        outcome = await act_stage_step(svc, session, response)

        assert outcome.tool_dispatches == response.tool_calls
        assert outcome.context_appends == []
        assert outcome.advance_iteration is False
        assert session.state == SessionState.ACTIVE  # mark_waiting is the engine's
        assert not session.has_pending_tools


class TestActCollectStage:
    """act collect half: reply semantics / completion declared as Outcome data."""

    async def test_nothing_completed_is_noop(self):
        svc = _make_services(MagicMock())

        outcome = await act_stage_collect(svc, _make_session())

        assert outcome == StageOutcome()

    async def test_send_email_complete_declares_reply_and_completion(self):
        publish = AsyncMock()
        logger = MagicMock()
        svc = _make_services(MagicMock(), logger=logger, publish=publish)
        session = _make_session()
        trigger = _make_message("问题")
        session.trigger_message = trigger
        tc = ToolCall(
            id="tc-1",
            function_name="sendEmail",
            arguments={"content": "Done!", "is_complete": True},
        )
        session.set_last_llm_response(LLMResponse(content=None, tool_calls=[tc]))
        result = ToolResult(success=True, data={"content": "Done!", "is_complete": True})
        await _dispatch_finished(session, tc, result)

        outcome = await act_stage_collect(svc, session)

        assert outcome.mails == [
            MailEffect(
                to=["user-001@tlon.internal"],
                content="Done!",
                in_reply_to=trigger.id,
                thread_id=trigger.thread_id,
            )
        ]
        assert outcome.context_appends[0] == {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "tc-1",
                    "type": "function",
                    "function": {"name": "sendEmail", "arguments": json.dumps(tc.arguments)},
                }
            ],
        }
        assert outcome.context_appends[1] == {
            "role": "tool",
            "tool_call_id": "tc-1",
            "content": json.dumps(result.to_dict()),
        }
        assert outcome.save_interaction == "Done!"
        assert outcome.run_on_complete is True
        assert outcome.complete is True
        # Session-local bookkeeping is the stage's input state...
        assert len(session.all_tool_results) == 1
        # ...but completion is the engine's to apply
        assert not session.is_completed
        # Observability went through services
        end_events = [
            c for c in publish.await_args_list if c.args[0] == EventType.TOOL_EXECUTE_END
        ]
        assert len(end_events) == 1
        assert end_events[0].kwargs["payload"] == {
            "agent_id": "a-001",
            "tool_name": "sendEmail",
            "success": True,
        }
        info_events = [c.args[0] for c in logger.info.call_args_list]
        assert "session_completed" in info_events

    async def test_explicit_to_wins_over_trigger_sender(self):
        svc = _make_services(MagicMock())
        session = _make_session()
        trigger = _make_message("问题")
        session.trigger_message = trigger
        tc = ToolCall(id="tc-1", function_name="sendEmail", arguments={})
        result = ToolResult(
            success=True,
            data={"to": ["other@tlon.internal"], "content": "转发", "is_complete": False},
        )
        await _dispatch_finished(session, tc, result)

        outcome = await act_stage_collect(svc, session)

        assert outcome.mails[0].to == ["other@tlon.internal"]
        assert outcome.mails[0].in_reply_to == trigger.id
        assert outcome.complete is False
        assert outcome.run_on_complete is False
        # Not complete and nothing pending: the session reactivates directly
        assert session.iteration == 1
        assert session.state == SessionState.ACTIVE

    async def test_failed_tool_publishes_error_event_and_no_mail(self):
        publish = AsyncMock()
        svc = _make_services(MagicMock(), publish=publish)
        session = _make_session()
        tc = ToolCall(id="tc-1", function_name="sendEmail", arguments={})
        result = ToolResult(success=False, data={}, error="boom")
        await _dispatch_finished(session, tc, result)

        outcome = await act_stage_collect(svc, session)

        assert outcome.mails == []
        assert outcome.complete is False
        event_types = [c.args[0] for c in publish.await_args_list]
        assert EventType.TOOL_EXECUTE_END in event_types
        assert EventType.TOOL_ERROR in event_types
        error_event = [
            c for c in publish.await_args_list if c.args[0] == EventType.TOOL_ERROR
        ][0]
        assert error_event.kwargs["payload"] == {
            "agent_id": "a-001",
            "tool_name": "sendEmail",
            "error": "boom",
        }

    async def test_non_send_email_tool_declares_no_mail(self):
        svc = _make_services(MagicMock())
        session = _make_session()
        tc = ToolCall(id="tc-1", function_name="searchMemories", arguments={"query": "q"})
        result = ToolResult(success=True, data={"hits": []})
        await _dispatch_finished(session, tc, result)

        outcome = await act_stage_collect(svc, session)

        assert outcome.mails == []
        assert outcome.save_interaction is None
        assert len(outcome.context_appends) == 2
        assert session.state == SessionState.ACTIVE


class TestDriverWithoutAgent:
    """C2 decoupling proof: the LoopDriver runs on injected capabilities alone."""

    async def test_think_act_complete_round_without_agent(self):
        """A full think -> act -> collect -> complete round, no Agent anywhere."""
        spec = _standard_spec()
        identity = AgentIdentity(id="a-001", name="TestAgent", address="a-001@tlon.internal")
        llm = MagicMock()
        llm.chat = AsyncMock(
            return_value=LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="tc-1",
                        function_name="sendEmail",
                        arguments={"content": "Done!", "is_complete": True},
                    )
                ],
            )
        )
        publish = AsyncMock()
        services = _make_services(llm, publish=publish)
        memory = AsyncMock()
        reflection = MagicMock()
        mailbox = AsyncMock()
        executor = _make_executor(
            publish=publish, memory=memory, reflection=reflection, mailbox=mailbox
        )
        tool_executor = ToolExecutor()
        send_tool = MagicMock()
        send_tool.name = "sendEmail"
        send_tool.execute = AsyncMock(
            return_value=ToolResult(success=True, data={"content": "Done!", "is_complete": True})
        )
        tool_executor.register_tool(send_tool)
        on_complete = MagicMock(run=AsyncMock())
        driver = LoopDriver(
            identity=identity,
            spec=spec,
            services=services,
            executor=executor,
            tool_executor=tool_executor,
            per_tool_timeout=30.0,
            on_complete=on_complete,
        )

        mail = _make_message()
        await driver.process_message(mail)

        # The reply went out through the executor's publish closure
        mail_events = [c for c in publish.await_args_list if c.args[0] == EventType.MAIL_NEW]
        assert len(mail_events) == 1
        reply = mail_events[0].kwargs["payload"]
        assert reply.content == "Done!"
        assert reply.to == [mail.from_address]
        assert reply.in_reply_to == mail.id
        assert reply.thread_id == mail.thread_id
        # Effects: interaction saved, on-complete ran, cursor advanced
        memory.add_interaction.assert_awaited_once()
        reflection.note_interaction.assert_called_once()
        on_complete.run.assert_awaited_once()
        mailbox.mark_read.assert_awaited_once_with(mail.id)
        # State derivation closure saw the session open and close
