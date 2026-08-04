"""Tests for on-complete action expressions (DSL phase 4b).

Covers: ``:actions`` schema validation, evaluation purity (actions are
collected as data, never executed by the evaluator), execution semantics
(spawn/emit delegation), failure isolation, trust/budget passthrough,
and the LoopDriver completion integration.
"""

import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.contextvars import bound_contextvars

from tlon.agents.agent import Agent
from tlon.agents.edn import Form, loads
from tlon.agents.eval import (
    ACTIONS_BUILTINS,
    ActionRequest,
    EvalError,
    eval_form,
    make_actions_builtins,
    validate_action_form,
)
from tlon.agents.on_complete import OnCompleteRunner
from tlon.agents.session import AgentSession
from tlon.agents.spawn_errors import SpawnRejectedError
from tlon.agents.spec import AgentSpec, SpecValidationError, standard_behavior_edn
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.config import AgentConfig
from tlon.base.events.event import EventType
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.organization.mail.mail import Mail
from tlon.tools.base import ToolResult


def _msg(content: str = "hello") -> Mail:
    return Mail.create(
        from_address="u-1@tlon.internal",
        to=["a-001@tlon.internal"],
        content=content,
    )


_ENV = {
    "msg": None,  # filled per test
    "result": "本轮处理完毕",
    "self": {"id": "a-001", "name": "小特"},
}


def _env(msg: Mail | None = None, result: str = "本轮处理完毕") -> dict:
    return {**_ENV, "msg": msg if msg is not None else _msg(), "result": result}


def _make_agent(**kwargs) -> Agent:
    """Build an Agent with all deps as mocks (mirrors test_agent_react_loop)."""
    config = AgentConfig(id="a-001", name="小特")

    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    event_bus.subscribe = MagicMock(return_value="sub-id")
    event_bus.unsubscribe = MagicMock()

    defaults = {
        "config": config,
        "event_bus": event_bus,
        "llm_client": MagicMock(),
        "memory": AsyncMock(),
        "task_manager": MagicMock(),
        "tool_executor": ToolExecutor(),
    }
    defaults.update(kwargs)
    return Agent(**defaults)


def _standard_spec() -> AgentSpec:
    """The canonical complete spec (standard ReAct program, no actions)."""
    return AgentSpec.from_edn(loads(standard_behavior_edn()))


def _spec_with_actions(*sources: str) -> AgentSpec:
    """The standard spec with on-complete actions injected."""
    spec = _standard_spec()
    return dataclasses.replace(
        spec,
        on_complete=dataclasses.replace(
            spec.on_complete, actions=tuple(loads(source) for source in sources)
        ),
    )


def _edn_with_actions(actions: str) -> str:
    """The standard behavior EDN with an :actions vector in :on-complete."""
    return standard_behavior_edn().replace(
        ':on-complete {:save-interaction true'
        ' :reflect {:enabled true :interval-minutes 15}}',
        ':on-complete {:save-interaction true'
        ' :reflect {:enabled true :interval-minutes 15}'
        f' :actions {actions}}}',
    )


def _make_session(message: Mail | None = None) -> AgentSession:
    session = AgentSession(session_id="sess-1", trigger_content="hello")
    session.trigger_message = message if message is not None else _msg()
    return session


class TestActionsSchema:
    def test_actions_parse(self):
        spec = AgentSpec.from_edn(loads(_edn_with_actions(
            '[(emit "done" [])'
            ' (when (content-matches? result "完成") (spawn "archivist" {:name "归档员"}))]'
        )))
        assert len(spec.on_complete.actions) == 2
        assert all(isinstance(form, Form) for form in spec.on_complete.actions)

    def test_actions_default_empty(self):
        spec = _standard_spec()
        assert spec.on_complete.actions == ()

    def test_actions_must_be_vector(self):
        with pytest.raises(SpecValidationError, match=":actions must be a vector"):
            AgentSpec.from_edn(loads(_edn_with_actions('(emit "x" [])')))

    def test_actions_item_must_be_form(self):
        with pytest.raises(SpecValidationError, match="must be expressions"):
            AgentSpec.from_edn(loads(_edn_with_actions('[42]')))

    def test_unknown_action_rejected(self):
        with pytest.raises(SpecValidationError, match="unknown action"):
            AgentSpec.from_edn(loads(_edn_with_actions('[(delete "x")]')))

    def test_action_in_condition_position_rejected(self):
        with pytest.raises(SpecValidationError, match="unknown function or special form"):
            AgentSpec.from_edn(loads(_edn_with_actions('[(when (spawn "a") (emit "x" []))]')))

    def test_non_action_top_level_rejected(self):
        with pytest.raises(SpecValidationError, match="unknown action"):
            AgentSpec.from_edn(loads(_edn_with_actions(
                '[(content-matches? result "x")]'
            )))

    def test_undefined_symbol_rejected(self):
        with pytest.raises(SpecValidationError, match="undefined symbol"):
            AgentSpec.from_edn(loads(_edn_with_actions('[(emit unknown [])]')))

    def test_nested_when_rejected(self):
        with pytest.raises(SpecValidationError, match="unknown action"):
            AgentSpec.from_edn(loads(_edn_with_actions(
                '[(when true (when true (emit "x" [])))]'
            )))

    def test_to_dict_from_dict_roundtrip(self):
        spec = AgentSpec.from_edn(loads(_edn_with_actions(
            '[(emit "done" [])'
            ' (when (content-matches? result "完成") (spawn "archivist" {:name "归档员"}))]'
        )))
        restored = AgentSpec.from_dict(spec.to_dict())
        assert restored.on_complete.actions == spec.on_complete.actions


class TestValidateActionForm:
    def test_action_form_ok(self):
        validate_action_form(loads('(spawn "archivist")'), scope={"msg", "result", "self"})
        validate_action_form(loads('(emit "done" [])'), scope={"msg", "result", "self"})

    def test_when_with_pure_condition_ok(self):
        validate_action_form(
            loads('(when (content-matches? msg "hi") (emit "done" []) (spawn "a"))'),
            scope={"msg", "result", "self"},
        )

    def test_non_form_rejected(self):
        with pytest.raises(EvalError, match="must be an action form"):
            validate_action_form(42, scope={"msg"})


class TestEvaluationPurity:
    """Evaluating action expressions performs zero side effects."""

    def test_spawn_emit_collected_as_data(self):
        collector: list[ActionRequest] = []
        builtins = make_actions_builtins(collector)
        form = loads(
            '(when (content-matches? result "完毕")'
            '  (spawn "archivist" {:name "归档员"})'
            '  (emit "本轮处理完毕" []))'
        )
        result = eval_form(form, _env(), builtins)
        assert result is None
        assert collector == [
            ActionRequest(kind="spawn", data={"prototype": "archivist", "name": "归档员"}),
            ActionRequest(kind="emit", data={"content": "本轮处理完毕", "to": []}),
        ]

    def test_condition_false_collects_nothing(self):
        collector: list[ActionRequest] = []
        eval_form(
            loads('(when (content-matches? result "不存在") (spawn "archivist"))'),
            _env(),
            make_actions_builtins(collector),
        )
        assert collector == []

    def test_actions_registry_validates_but_collects_nothing(self):
        # ACTIONS_BUILTINS has no collector: usable for validate, and even
        # evaluation performs no observable effect.
        validate_action_form(loads('(spawn "a")'), scope={"msg", "result", "self"})
        assert ACTIONS_BUILTINS["spawn"].pure is False

    def test_content_matches_accepts_string_result(self):
        collector: list[ActionRequest] = []
        assert eval_form(
            loads('(content-matches? result "完毕")'),
            _env(result="本轮处理完毕"),
            make_actions_builtins(collector),
        ) is True

    def test_spawn_arg_validation_at_eval(self):
        with pytest.raises(EvalError, match="spawn prototype must be a string"):
            eval_form(loads("(spawn 42)"), _env(), make_actions_builtins([]))
        with pytest.raises(EvalError, match="unknown keys"):
            eval_form(
                loads('(spawn "a" {:tools ["shell"]})'), _env(), make_actions_builtins([])
            )

    def test_emit_arg_validation_at_eval(self):
        with pytest.raises(EvalError, match="emit content must be a string"):
            eval_form(loads("(emit 42 [])"), _env(), make_actions_builtins([]))


class TestExecution:
    async def test_condition_true_spawns(self):
        registry = MagicMock()
        registry.spawn_contractor = AsyncMock()
        agent = _make_agent(
            spec=_spec_with_actions(
                '(when (content-matches? result "完毕") (spawn "archivist" {:name "归档员"}))'
            ),
            registry=registry,
        )
        with bound_contextvars(session_id="sess-1"):
            await OnCompleteRunner(agent).run(_make_session(), "本轮处理完毕")
        registry.spawn_contractor.assert_awaited_once_with(
            prototype="archivist",
            name="归档员",
            spawner_id="a-001",
            session_id="sess-1",
        )

    async def test_condition_false_no_calls(self):
        registry = MagicMock()
        registry.spawn_contractor = AsyncMock()
        agent = _make_agent(
            spec=_spec_with_actions('(when (content-matches? result "不存在") (spawn "a"))'),
            registry=registry,
        )
        agent._send_mail = AsyncMock()
        await OnCompleteRunner(agent).run(_make_session(), "本轮处理完毕")
        registry.spawn_contractor.assert_not_awaited()
        agent._send_mail.assert_not_awaited()

    async def test_emit_sends_mail(self):
        agent = _make_agent(spec=_spec_with_actions('(emit "本轮处理完毕" ["u-1@tlon.internal"])'))
        agent._send_mail = AsyncMock()
        session = _make_session()
        await OnCompleteRunner(agent).run(session, "result")
        agent._send_mail.assert_awaited_once_with(
            to=["u-1@tlon.internal"],
            content="本轮处理完毕",
            in_reply_to=session.trigger_message.id,
            thread_id=session.trigger_message.thread_id,
        )

    async def test_emit_without_recipients_replies_to_sender(self):
        """An emit with an empty recipient vector replies to the trigger
        mail's sender (thread headers inherited)."""
        agent = _make_agent(spec=_spec_with_actions('(emit "收到" [])'))
        agent._send_mail = AsyncMock()
        session = _make_session()
        await OnCompleteRunner(agent).run(session, "result")
        agent._send_mail.assert_awaited_once_with(
            to=["u-1@tlon.internal"],
            content="收到",
            in_reply_to=session.trigger_message.id,
            thread_id=session.trigger_message.thread_id,
        )

    async def test_spawn_without_name_generates_default(self):
        registry = MagicMock()
        registry.spawn_contractor = AsyncMock()
        agent = _make_agent(spec=_spec_with_actions('(spawn "archivist")'), registry=registry)
        await OnCompleteRunner(agent).run(_make_session(), "result")
        kwargs = registry.spawn_contractor.await_args.kwargs
        assert kwargs["name"].startswith("archivist-")

    async def test_no_actions_is_noop(self):
        agent = _make_agent(spec=_standard_spec())
        agent._send_mail = AsyncMock()
        await OnCompleteRunner(agent).run(_make_session(), "result")
        agent._send_mail.assert_not_awaited()


class TestIsolation:
    async def test_failing_action_does_not_block_others(self):
        registry = MagicMock()
        registry.spawn_contractor = AsyncMock(side_effect=RuntimeError("boom"))
        agent = _make_agent(
            spec=_spec_with_actions('(spawn "a")', '(emit "still sent" [])'),
            registry=registry,
        )
        agent._send_mail = AsyncMock()
        session = _make_session()
        await OnCompleteRunner(agent).run(session, "result")  # no raise
        agent._send_mail.assert_awaited_once_with(
            to=["u-1@tlon.internal"],
            content="still sent",
            in_reply_to=session.trigger_message.id,
            thread_id=session.trigger_message.thread_id,
        )

    async def test_eval_failure_only_logged(self):
        # A runtime-only failure (bad regex) in one form does not stop the
        # next form's actions from being collected and executed.
        agent = _make_agent(
            spec=_spec_with_actions(
                '(when (content-matches? result "(") (spawn "a"))',
                '(emit "still sent" [])',
            ),
            registry=MagicMock(),
        )
        agent._send_mail = AsyncMock()
        session = _make_session()
        await OnCompleteRunner(agent).run(session, "result")  # no raise
        agent._send_mail.assert_awaited_once_with(
            to=["u-1@tlon.internal"],
            content="still sent",
            in_reply_to=session.trigger_message.id,
            thread_id=session.trigger_message.thread_id,
        )

    async def test_spawn_without_registry_logged_not_raised(self):
        agent = _make_agent(spec=_spec_with_actions('(spawn "a")', '(emit "ok" [])'))
        agent._send_mail = AsyncMock()
        session = _make_session()
        await OnCompleteRunner(agent).run(session, "result")  # no raise
        agent._send_mail.assert_awaited_once_with(
            to=["u-1@tlon.internal"],
            content="ok",
            in_reply_to=session.trigger_message.id,
            thread_id=session.trigger_message.thread_id,
        )

    async def test_untrusted_prototype_rejected_and_logged(self):
        registry = MagicMock()
        registry.spawn_contractor = AsyncMock(
            side_effect=SpawnRejectedError("untrusted_prototype", "staged, awaiting approval")
        )
        agent = _make_agent(
            spec=_spec_with_actions('(spawn "staged-proto")', '(emit "ok" [])'),
            registry=registry,
        )
        agent._send_mail = AsyncMock()
        session = _make_session()
        await OnCompleteRunner(agent).run(session, "result")  # no raise
        registry.spawn_contractor.assert_awaited_once()
        agent._send_mail.assert_awaited_once_with(
            to=["u-1@tlon.internal"],
            content="ok",
            in_reply_to=session.trigger_message.id,
            thread_id=session.trigger_message.thread_id,
        )


class TestLoopDriverIntegration:
    async def test_session_completion_runs_actions_once(self):
        """A sendEmail(is_complete=true) round triggers the actions once."""
        llm = MagicMock()
        llm.chat = AsyncMock(return_value=LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="tc-1",
                    function_name="sendEmail",
                    arguments={"content": "Done!", "is_complete": True},
                )
            ],
        ))

        registry = MagicMock()
        registry.spawn_contractor = AsyncMock()
        agent = _make_agent(
            llm_client=llm,
            spec=_spec_with_actions(
                '(emit "本轮处理完毕" [])',
                '(when (content-matches? result "Done") (spawn "archivist" {:name "归档员"}))',
            ),
            registry=registry,
        )

        mock_tool = MagicMock()
        mock_tool.name = "sendEmail"
        mock_tool.execute = AsyncMock(return_value=ToolResult(
            success=True,
            data={"content": "Done!", "is_complete": True},
        ))
        agent.tool_executor.register_tool(mock_tool)

        await agent._process_message(_msg("hi"))

        # spawn ran with the session id attributed from the session span
        registry.spawn_contractor.assert_awaited_once()
        kwargs = registry.spawn_contractor.await_args.kwargs
        assert kwargs["prototype"] == "archivist"
        assert kwargs["name"] == "归档员"
        assert kwargs["spawner_id"] == "a-001"
        assert kwargs["session_id"].startswith("a-001-msg-")

        # Two outbound messages: the final reply + the emit action
        message_events = [
            call
            for call in agent.event_bus.publish.await_args_list
            if call.args[0].type == EventType.MAIL_NEW
        ]
        assert len(message_events) == 2
        contents = {call.args[0].payload.content for call in message_events}
        assert contents == {"Done!", "本轮处理完毕"}

    async def test_spec_without_actions_runs_no_actions(self):
        llm = MagicMock()
        llm.chat = AsyncMock(return_value=LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id="tc-1",
                    function_name="sendEmail",
                    arguments={"content": "Done!", "is_complete": True},
                )
            ],
        ))
        agent = _make_agent(llm_client=llm, spec=_standard_spec())

        mock_tool = MagicMock()
        mock_tool.name = "sendEmail"
        mock_tool.execute = AsyncMock(return_value=ToolResult(
            success=True,
            data={"content": "Done!", "is_complete": True},
        ))
        agent.tool_executor.register_tool(mock_tool)

        await agent._process_message(_msg("hi"))

        message_events = [
            call
            for call in agent.event_bus.publish.await_args_list
            if call.args[0].type == EventType.MAIL_NEW
        ]
        assert len(message_events) == 1
