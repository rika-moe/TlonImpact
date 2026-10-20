"""Blocked drafts never become delivery facts, including after budget exhaustion."""

from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from tests.agents.test_completion_gate import GATED_EDN, _drive, _make_driver, _tc
from tests.mail_helpers import make_office
from tlon.agents.executor import EffectExecutor
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.session import AgentSession
from tlon.agents.session_journal import SessionJournal
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import LLMResponse
from tlon.base.paths import TlonPaths
from tlon.lifecycle.service import LifecycleService
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.lifecycle import make_lifecycle_operations
from tlon.operations.mail import make_mail_operations
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.member_mail import MemberMailService
from tlon.organization.mail.post_office import PostOffice


def _drafts() -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[
            _tc(
                "draft-reader",
                "mail-send",
                {
                    "to": ["user@tlon.internal"],
                    "subject": "未通过的回复",
                    "content": "未发送原稿：错误地说已给同伴发信",
                },
            ),
            _tc(
                "draft-peer",
                "mail-send",
                {
                    "to": ["peer@tlon.internal"],
                    "subject": "未通过的请教",
                    "content": "未发送原稿：给同伴的请教正文",
                },
            ),
        ],
    )


def _real_mail_engine(
    tmp_path: Path,
    responses: list[LLMResponse],
    *,
    max_iterations: int = 10,
) -> tuple[LoopDriver, SessionJournal, MagicMock, PostOffice, AgentSession]:
    with patch(
        "tests.agents.test_completion_gate.GATED_EDN",
        GATED_EDN.replace(":max-iterations 10", f":max-iterations {max_iterations}"),
    ):
        driver, journal, _, services = _make_driver(tmp_path, responses)
    paths = TlonPaths(tmp_path)
    bus = EventBus()
    bus.publish = AsyncMock()
    office = make_office(bus, tmp_path, ["agent1", "peer"])
    office.store.register_member("human:user")
    tools = ToolExecutor()
    operations = OperationExecutor(
        OperationRegistry(
            make_mail_operations(
                MemberMailService("agent1", Mailbox(paths.mail_database_file(), "agent1"), office)
            )
        ),
        CallContext("agent1", "assembly", "assembly"),
    )
    for operation in operations.registry.definitions():
        tools.register_tool(OperationTool(operations, operation.name))
    controls = OperationExecutor(
        OperationRegistry(make_lifecycle_operations(LifecycleService("agent1"))),
        CallContext("agent1", "assembly", "assembly"),
    )
    tools.register_tool(OperationTool(controls, "finish-turn"))
    driver._tool_executor = tools
    driver._executor = EffectExecutor(
        identity=driver._identity,
        mailbox=Mailbox(paths.mail_database_file(), "agent1"),
        publish=AsyncMock(),
        logger=MagicMock(),
        post_office=office,
    )
    source = Mail.create(
        from_address="user@tlon.internal", to=["agent1@tlon.internal"], content="请回答并请教同伴"
    )
    office.store.add_to_inbox(source, "agent1")
    session = AgentSession(
        session_id="unsent-regression", trigger_content=source.content, responsibility=[source]
    )
    return driver, journal, services, office, session


async def test_rejected_multi_mail_draft_survives_revision_without_becoming_sent(
    tmp_path: Path,
) -> None:
    corrected = LLMResponse(
        content=None,
        tool_calls=[
            _tc(
                "corrected-reader",
                "mail-send",
                {
                    "to": ["user@tlon.internal"],
                    "content": "修订后回复：同伴信尚未发送",
                },
            )
        ],
    )
    driver, journal, services, office, session = _real_mail_engine(
        tmp_path,
        [
            _drafts(),
            LLMResponse(content="FAIL\n前函有误，请重发给同伴。"),
            corrected,
            LLMResponse(content="PASS"),
        ],
    )
    thoughts: list[list[dict[str, Any]]] = []
    original_think = services.think

    async def capture_think(context: list[dict[str, Any]], **kwargs: Any) -> LLMResponse:
        if kwargs.get("span") == "agent.think":
            thoughts.append(deepcopy(context))
        return await original_think(context, **kwargs)

    services.think = AsyncMock(side_effect=capture_think)
    await _drive(driver, session)
    next_think = thoughts[1]
    feedback = "\n".join(
        str(message.get("content") or "") for message in next_think if message["role"] == "user"
    )
    assert "未发送原稿：错误地说已给同伴发信" in feedback
    assert "未发送原稿：给同伴的请教正文" in feedback
    assert "draft-reader" in feedback and "draft-peer" in feedback
    assert "user@tlon.internal" in feedback and "peer@tlon.internal" in feedback
    assert "未执行" in feedback or "未派发" in feedback
    assert not any(message.get("tool_calls") for message in next_think)
    assert [mail.content for mail in office.store.mails("human:user")] == [
        "修订后回复：同伴信尚未发送"
    ]
    assert office.store.mails("peer") == []
    assert len(office.store.mails("agent1", "sent")) == 1
    assert session.is_completed
    assert await journal.derive_context(session.id) == session.context


async def test_exhausted_rejected_draft_produces_only_factual_runtime_notice(
    tmp_path: Path,
) -> None:
    driver, journal, services, office, session = _real_mail_engine(
        tmp_path,
        [
            _drafts(),
            LLMResponse(content="FAIL\n已发送的前函应更正。"),
        ],
        max_iterations=1,
    )
    await _drive(driver, session)
    notices = office.store.mails("human:user")
    assert len(notices) == 1
    assert notices[0].subject and "通知" in notices[0].subject
    assert "未发送原稿" not in notices[0].content
    assert "前函" not in notices[0].content
    assert "0" in notices[0].content
    assert "未完成" in notices[0].content
    assert office.store.mails("peer") == []
    assert len(office.store.mails("agent1", "sent")) == 1
    assert len(session.all_tool_results) == 0
    assert services.think.await_count == 2
    assert await journal.end_reason(session.id) == "max_iterations"
    assert await journal.derive_context(session.id) == session.context
