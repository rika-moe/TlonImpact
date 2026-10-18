"""Engine mail results are backed by the real post-office transaction."""

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.agents.context_pressure import ContextPressure
from tlon.agents.executor import EffectExecutor
from tlon.agents.loop_driver import LoopDriver
from tlon.agents.session import AgentSession
from tlon.agents.session_journal import SessionJournal
from tlon.agents.stages.act import act_stage_collect
from tlon.agents.tool_executor import ToolExecutor
from tlon.agents.trigger import AgentIdentity
from tlon.base.config import AgentConfig
from tlon.base.events.event_bus import EventBus
from tlon.base.llm_client import ToolCall
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.post_office import PostOffice
from tlon.organization.mail.resolver import RecipientResolver
from tlon.organization.mail.store import MailStore
from tlon.tools.mail.send_email import SendEmailTool
from tlon.tools.spill import ToolSpill


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncGenerator[tuple[LoopDriver, PostOffice, MagicMock]]:
    paths = TlonPaths(home=tmp_path)
    bus = EventBus()
    await bus.initialize()
    provider = MagicMock()
    configs = {"agent1": AgentConfig(id="agent1", name="Agent")}
    provider.get_config.side_effect = configs.get
    provider.get_all_ids.return_value = set(configs)
    resolver = RecipientResolver(
        provider,
        paths,
        external_owner=lambda address: "gateway:test" if address == "issue@example.com" else None,
    )
    office = PostOffice(bus, resolver, paths)
    office.store.register_member("human:bob")
    identity = AgentIdentity(id="agent1", name="Agent", address="agent1@tlon.internal")
    rule = MagicMock()
    rule.loop.fuel = None
    services = MagicMock()
    services.identity = identity
    services.publish_event = AsyncMock()
    executor = EffectExecutor(
        identity=identity,
        rule=rule,
        mailbox=Mailbox(paths.mail_database_file(), "agent1"),
        publish=AsyncMock(),
        logger=MagicMock(),
        post_office=office,
    )
    tools = ToolExecutor()
    tools.register_tool(SendEmailTool())
    journal = SessionJournal("agent1", paths)
    on_complete = MagicMock()
    on_complete.run = AsyncMock()
    driver = LoopDriver(
        identity=identity,
        rule=rule,
        services=services,
        executor=executor,
        tool_executor=tools,
        per_tool_timeout=5,
        on_complete=on_complete,
        journal=journal,
        spill=ToolSpill(paths, "agent1"),
        pressure=ContextPressure(MagicMock(), journal, 262144),
        is_human_sender=lambda address: True,
    )
    try:
        yield driver, office, services
    finally:
        await bus.shutdown()


def _call(
    *, recipient: str = "bob", request_id: str | None = None, call_id: str = "tool1"
) -> ToolCall:
    args = {"to": [recipient], "content": "completed work"}
    if request_id is not None:
        args["request_id"] = request_id
    return ToolCall(id=call_id, function_name="sendEmail", arguments=args)


async def test_tool_receipt_is_durable_before_collect_and_replay_does_not_send_again(
    engine: tuple[LoopDriver, PostOffice, MagicMock],
) -> None:
    driver, office, services = engine
    call = _call()
    completed = await driver.execute_one_tool("session1", call)
    result = completed[1]
    assert result.success
    assert result.data["request_id"] == "session1:tool1"
    fresh_store = MailStore(office.store.database)
    assert [mail.id for mail in fresh_store.mails("human:bob")] == [result.data["mail_id"]]
    session = AgentSession(session_id="session1", trigger_content="work")
    outcome = await act_stage_collect(services, session, [completed])
    assert not outcome.complete
    await driver._apply_outcome(session, None, outcome)
    assert not session.is_completed
    assert len(fresh_store.mails("human:bob")) == 1
    replay = await driver.execute_one_tool("session1", call)
    assert replay[1].data["mail_id"] == result.data["mail_id"]
    assert len(fresh_store.mails("human:bob")) == 1


@pytest.mark.parametrize("recipient", ["unknown", "bob"])
async def test_rejection_never_completes_session(
    engine: tuple[LoopDriver, PostOffice, MagicMock],
    recipient: str,
) -> None:
    driver, office, services = engine
    if recipient == "bob":
        office.store.retire("human:bob")
    completed = await driver.execute_one_tool("session1", _call(recipient=recipient))
    assert not completed[1].success
    assert completed[1].data["request_id"] == "session1:tool1"
    session = AgentSession(session_id="session1", trigger_content="work")
    outcome = await act_stage_collect(services, session, [completed])
    assert not outcome.complete
    await driver._apply_outcome(session, None, outcome)
    assert not session.is_completed
    assert office.store.mails("human:bob") == []
    assert office.store.mails("agent1", "sent") == []


async def test_timeout_after_real_commit_preserves_key_and_safe_retry(
    engine: tuple[LoopDriver, PostOffice, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver, office, _ = engine
    original_notify = office._notify
    committed = asyncio.Event()

    async def blocked_notify(*args: object) -> None:
        committed.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(office, "_notify", blocked_notify)
    driver._per_tool_timeout = 0.2
    completed = await driver.execute_one_tool("session1", _call())
    assert committed.is_set(), "timeout should occur after the real database commit"
    assert not completed[1].success
    assert completed[1].data == {"request_id": "session1:tool1", "outcome": "unknown"}
    assert len(office.store.mails("human:bob")) == 1
    monkeypatch.setattr(office, "_notify", original_notify)
    replay = await driver.execute_one_tool(
        "session2",
        _call(request_id="session1:tool1", call_id="retry"),
    )
    assert replay[1].success
    assert len(office.store.mails("human:bob")) == 1


async def test_gateway_failure_is_reported_in_actual_tool_receipt(
    engine: tuple[LoopDriver, PostOffice, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver, office, _ = engine
    original_append = Mailbox.append_inbox

    async def fail_gateway(mailbox: Mailbox, mail: Mail, *, gateway: bool = False) -> None:
        if mailbox.member_id == "gateway:test":
            raise OSError("gateway unavailable")
        await original_append(mailbox, mail, gateway=gateway)

    monkeypatch.setattr(Mailbox, "append_inbox", fail_gateway)
    _, result = await driver.execute_one_tool("session1", _call(recipient="issue@example.com"))
    assert result.success  # Gateway is explicitly best effort.
    assert result.data["local_recipients"] == []
    assert result.data["gateways"] == [
        {
            "address": "issue@example.com",
            "status": "failed",
            "error": "gateway unavailable",
        }
    ]
    assert "gateway unavailable" in result.message
    assert office.store.mails("gateway:test") == []
