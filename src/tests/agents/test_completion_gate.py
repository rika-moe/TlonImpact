"""Mail-delivery critique gates are independent of turn completion."""

import asyncio
import contextlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from tests.mail_helpers import make_office
from tests.recipe_helpers import recipe_for
from tlon.agents.context_builder import BuiltContext
from tlon.agents.context_pressure import ContextPressure
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.member_spec import MemberSpec, validate_member_spec
from tlon.agents.session import AgentSession, SessionState
from tlon.agents.session_journal import SessionJournal
from tlon.agents.stages.act import attempts_completion
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.trigger import AgentIdentity
from tlon.agents.vocabulary import STAGE_COND_BUILTINS
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMResponse, ToolCall
from tlon.base.paths import TlonPaths
from tlon.lifecycle.service import LifecycleService
from tlon.operations.adapters import OperationTool
from tlon.operations.control import control_names
from tlon.operations.core import CallContext, Operation, OperationExecutor, OperationRegistry
from tlon.operations.lifecycle import make_lifecycle_operations
from tlon.operations.mail import make_mail_operations
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.member_mail import MemberMailService
from tlon.tools.spill import ToolSpill
from tlosure.edn import loads

GATED_EDN = '{:version 1 :rules [{:name "respond" :on :mail-delivered :actions [(run-loop (quote {:context {:sections [(persona) (protocol)]} :stages [(think) (critique {:if (sending-mail?)}) (act {:if (or (not (sending-mail?)) (not (critique-failed?)))}) (revise {:if (critique-failed?)})] :until (or (iteration-limit-reached?) (error-limit-reached?)) :max-iterations 10 :max-consecutive-errors 3 :max-tokens 1000000 :reflect {:enabled false :interval-minutes 5}}))]}]}'


def _mail() -> Mail:
    return Mail.create(from_address="user@tlon.internal", content="hi", to=["agent1@tlon.internal"])


def _tc(tc_id: str, name: str = "someTool", arguments: dict | None = None) -> ToolCall:
    return ToolCall(id=tc_id, function_name=name, arguments=arguments or {})


def _completing(content: str, tc_id: str = "c-final") -> LLMResponse:
    return LLMResponse(
        content="done",
        tool_calls=[_tc(tc_id, "mail-send", {"to": ["user@tlon.internal"], "content": content})],
    )


def _make_driver(
    tmp_path: Path,
    responses: list[LLMResponse],
) -> tuple[LoopDriver, SessionJournal, MemberMailService, MagicMock]:
    spec = MemberSpec.from_edn(loads(GATED_EDN))
    validate_member_spec(spec)
    rule = spec.rules[0]

    services = MagicMock()
    services.logger = MagicMock()
    services.control_names.return_value = control_names()
    services.publish_event = AsyncMock()
    services.build_context = AsyncMock(
        return_value=BuiltContext(messages=[{"role": "user", "content": "hi"}], anchor_len=1)
    )
    services.think = AsyncMock(
        side_effect=[
            *responses,
            LLMResponse(content=None, tool_calls=[_tc("finish", "finish-turn")]),
        ]
    )
    services.session_span = lambda session: contextlib.nullcontext()

    executor = MagicMock()
    executor.publish = AsyncMock()

    paths = TlonPaths(home=tmp_path)
    bus = MagicMock(spec=EventBus)
    bus.publish = AsyncMock()
    mail_service = MemberMailService(
        "agent1", Mailbox(paths.mail_database_file(), "agent1"), make_office(bus, tmp_path)
    )
    mail_service.send = AsyncMock(wraps=mail_service.send)
    operations = OperationExecutor(
        OperationRegistry(
            (
                *make_mail_operations(mail_service),
                *make_lifecycle_operations(LifecycleService("agent1")),
            )
        ),
        CallContext("agent1", "test", "test"),
    )
    tool_executor = ToolExecutor()
    for operation in operations.registry.definitions():
        tool_executor.register_tool(OperationTool(operations, operation.name))

    async def work(context: CallContext, args: dict[str, Any]) -> dict[str, bool]:
        return {"ok": True}

    working = Operation(
        "someTool",
        "Test work",
        {
            "type": "object",
            "properties": {"a": {"type": "integer"}},
            "required": [],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        work,
        effects=frozenset({"read"}),
    )
    work_executor = OperationExecutor(
        OperationRegistry([working]), CallContext("agent1", "test", "test")
    )
    tool_executor.register_tool(OperationTool(work_executor, "someTool"))

    journal = SessionJournal("agent1", TlonPaths(home=tmp_path))
    driver = LoopDriver(
        identity=AgentIdentity(id="agent1", name="Agent", address="agent1@tlon.internal"),
        recipe=recipe_for(rule),
        services=services,
        executor=executor,
        tool_executor=tool_executor,
        journal=journal,
        spill=ToolSpill(TlonPaths(home=tmp_path), "agent1"),
        pressure=ContextPressure(MagicMock(), journal, 262144),
        is_human_sender=lambda address: True,
    )
    return driver, journal, mail_service, services


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


def _calls_by_span(services: MagicMock, span: str) -> list:
    return [c for c in services.think.await_args_list if c.kwargs.get("span") == span]


class TestAttemptsCompletion:
    """The predicate the engine exposes to stage conditions as
    ``round.completing``; must match act's completion/conflict rule."""

    def test_none_and_tool_less_responses_do_not_complete(self) -> None:
        assert attempts_completion(None) is False
        assert attempts_completion(LLMResponse(content="thinking")) is False

    def test_send_email_does_not_complete(self) -> None:
        assert attempts_completion(_completing("reply")) is False

    def test_finish_turn_alone_completes(self) -> None:
        response = LLMResponse(content=None, tool_calls=[_tc("c1", "finish-turn")])
        assert attempts_completion(response) is True

    def test_progress_mail_does_not_complete(self) -> None:
        response = LLMResponse(
            content=None,
            tool_calls=[_tc("c1", "mail-send", {"content": "wip"})],
        )
        assert attempts_completion(response) is False

    def test_conflicting_completion_does_not_complete(self) -> None:
        # A completion signal mixed with other tools is act's conflict
        # case: nothing is dispatched, the round does not complete.
        response = LLMResponse(
            content=None,
            tool_calls=[
                _tc("c1", "finish-turn"),
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
        session = AgentSession(session_id=f"agent1-{mail.id}", trigger_content=mail.content)

        await _drive(driver, session)

        assert session.is_completed
        executor.send.assert_awaited_once()
        assert executor.send.await_args.args[0]["content"] == "reply"
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
        session = AgentSession(session_id=f"agent1-{mail.id}", trigger_content=mail.content)

        # Round 1: the FAILed draft must never reach act's dispatch.
        await driver.step_session(session)
        assert session.state == SessionState.ACTIVE  # not WAITING: nothing dispatched
        executor.send.assert_not_awaited()
        # revise fed the feedback into context and consumed the verdict.
        assert any("没有回答来信的问题" in str(m.get("content")) for m in session.context)
        assert session.loop_scratch.get("critique") is None

        # Round 2: the revised draft is reviewed again and passes.
        await _drive(driver, session)

        assert session.is_completed
        executor.send.assert_awaited_once()
        assert executor.send.await_args.args[0]["content"] == "draft-2"
        # The revising think saw the feedback.
        second_think = _calls_by_span(services, "agent.think")[1].args[0]
        assert any("没有回答来信的问题" in str(m.get("content")) for m in second_think)
        # Journal and memory agree on the model-visible context.
        assert await journal.derive_context(session.id) == session.context

    async def test_working_rounds_pay_no_critique(self, tmp_path: Path) -> None:
        mail = _mail()
        working = LLMResponse(content="working", tool_calls=[_tc("c1", "someTool", {"a": 1})])
        driver, _, executor, services = _make_driver(
            tmp_path, [working, _completing("final"), LLMResponse(content="PASS")]
        )
        session = AgentSession(session_id=f"agent1-{mail.id}", trigger_content=mail.content)

        await _drive(driver, session)

        assert session.is_completed
        # Two thinks + exactly one critique (the completing round only).
        assert len(_calls_by_span(services, "agent.think")) == 3
        assert len(_calls_by_span(services, "agent.critique")) == 1

    async def test_conflicting_completion_skips_critique(self, tmp_path: Path) -> None:
        mail = _mail()
        conflict = LLMResponse(
            content=None,
            tool_calls=[
                _tc("c1", "finish-turn"),
                _tc("c2", "someTool"),
            ],
        )
        driver, _, _, services = _make_driver(
            tmp_path, [conflict, _completing("final"), LLMResponse(content="PASS")]
        )
        session = AgentSession(session_id=f"agent1-{mail.id}", trigger_content=mail.content)

        await _drive(driver, session)

        assert session.is_completed
        # The conflict round is not completing: act's conflict reminder
        # fired and no critique call was spent on it.
        assert any("冲突" in str(m.get("content")) for m in session.context)
        assert len(_calls_by_span(services, "agent.critique")) == 1

    async def test_unparseable_verdict_leaves_the_gate_open(self, tmp_path: Path) -> None:
        """semantics under the gate: an unknown review outcome is a
        skipped review, not a block — the reply still goes out."""
        mail = _mail()
        driver, _, executor, _ = _make_driver(
            tmp_path, [_completing("reply"), LLMResponse(content="我觉得还行")]
        )
        session = AgentSession(session_id=f"agent1-{mail.id}", trigger_content=mail.content)

        await _drive(driver, session)

        assert session.is_completed
        executor.send.assert_awaited_once()
        assert executor.send.await_args.args[0]["content"] == "reply"
        assert session.loop_scratch.get("critique") is None

    async def test_finish_decision_does_not_trigger_mail_critique(self, tmp_path: Path) -> None:
        driver, _, executor, services = _make_driver(
            tmp_path, [LLMResponse(content=None, tool_calls=[_tc("c1", "finish-turn")])]
        )
        session = AgentSession(session_id="agent1-finish", trigger_content="notice")
        await _drive(driver, session)
        assert session.is_completed
        executor.send.assert_not_awaited()
        assert not _calls_by_span(services, "agent.critique")
