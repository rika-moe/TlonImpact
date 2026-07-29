"""Tests for the agent's mail intake flow (docs/design/mail.md).

Covers:
- MAIL_DELIVERED targeting: only the copy addressed to this agent is
  enqueued; the agent's own mail is never enqueued
- Boot scan: initialize() enqueues every unread inbox mail (the
  redelivery replacement — the mailbox is the durable queue)
- Session completion advances the inbox cursor (mark_read)
"""

import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

from tlon.agents.agent import Agent
from tlon.agents.tool_executor import ToolExecutor
from tlon.base.config import AgentConfig
from tlon.base.events.event import Event, EventType
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox


def _make_agent(mailbox: Mailbox | None = None, **kwargs) -> Agent:
    """Build an Agent with all deps as mocks."""
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    event_bus.subscribe = MagicMock(return_value="sub-id")
    event_bus.unsubscribe = MagicMock()

    defaults = {
        "config": AgentConfig(id="a-001", name="TestAgent"),
        "event_bus": event_bus,
        "llm_client": MagicMock(),
        "memory": AsyncMock(),
        "task_manager": MagicMock(),
        "tool_executor": ToolExecutor(),
        "mailbox": mailbox,
    }
    defaults.update(kwargs)
    return Agent(**defaults)


def _mail(
    to: list[str] | None = None,
    from_address: str = "user-001@tlon.internal",
    content: str = "hi",
) -> Mail:
    return Mail.create(
        from_address=from_address,
        to=to or ["a-001@tlon.internal"],
        content=content,
    )


def _delivered(mail: Mail, recipient: str) -> Event:
    return Event(
        type=EventType.MAIL_DELIVERED,
        payload={"mail": mail, "recipient": recipient},
        source="mail_delivery",
    )


class TestMailDeliveredEnqueue:
    async def test_mail_for_me_is_enqueued(self):
        agent = _make_agent()
        await agent._on_mail_delivered(_delivered(_mail(), "a-001"))
        assert agent._mail_queue.qsize() == 1

    async def test_mail_for_someone_else_is_ignored(self):
        agent = _make_agent()
        await agent._on_mail_delivered(_delivered(_mail(), "a-002"))
        assert agent._mail_queue.qsize() == 0

    async def test_own_mail_is_ignored(self):
        agent = _make_agent()
        mail = _mail(from_address="a-001@tlon.internal")
        await agent._on_mail_delivered(_delivered(mail, "a-001"))
        assert agent._mail_queue.qsize() == 0

    async def test_mail_not_addressed_to_me_is_dropped_by_trigger(self):
        """A mail in my inbox where I'm neither To nor CC is not processed."""
        agent = _make_agent()
        mail = _mail(to=["other@tlon.internal"])
        await agent._on_mail_delivered(_delivered(mail, "a-001"))
        assert agent._mail_queue.qsize() == 0

    async def test_cc_mail_is_enqueued(self):
        agent = _make_agent()
        mail = Mail.create(
            from_address="user-001@tlon.internal",
            to=["other@tlon.internal"],
            cc=["a-001@tlon.internal"],
            content="抄送",
        )
        await agent._on_mail_delivered(_delivered(mail, "a-001"))
        assert agent._mail_queue.qsize() == 1

    async def test_bad_payload_is_ignored(self):
        agent = _make_agent()
        await agent._on_mail_delivered(
            Event(type=EventType.MAIL_DELIVERED, payload={"not": "mail"}, source="x")
        )
        assert agent._mail_queue.qsize() == 0


class TestBootUnreadScan:
    @pytest.fixture
    def mailbox(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Mailbox(TlonPaths(home=tmpdir).member_mail_dir("a-001"))

    async def test_initialize_enqueues_unread_mail(self, mailbox: Mailbox):
        first = _mail(content="第一封")
        second = _mail(content="第二封")
        await mailbox.append_inbox(first)
        await mailbox.append_inbox(second)
        # An already-read mail must not be re-enqueued
        read = _mail(content="已读")
        await mailbox.append_inbox(read)
        await mailbox.mark_read(read.id)

        agent = _make_agent(mailbox=mailbox)
        await agent.initialize()
        try:
            assert agent._mail_queue.qsize() == 2
            queued = [agent._mail_queue.get_nowait()[0] for _ in range(2)]
            assert {m.id for m in queued} == {first.id, second.id}
        finally:
            await agent.shutdown()

    async def test_initialize_without_mailbox_skips_scan(self):
        agent = _make_agent(mailbox=None)
        await agent.initialize()
        try:
            assert agent._mail_queue.qsize() == 0
        finally:
            await agent.shutdown()


class TestMarkReadOnCompletion:
    async def test_mark_mail_read_advances_cursor(self):
        mailbox = AsyncMock()
        agent = _make_agent(mailbox=mailbox)
        mail = _mail()

        await agent._mark_mail_read(mail)

        mailbox.mark_read.assert_awaited_once_with(mail.id)

    async def test_mark_mail_read_tolerates_missing_mailbox(self):
        agent = _make_agent(mailbox=None)
        await agent._mark_mail_read(_mail())  # must not raise

    async def test_mark_mail_read_none_is_noop(self):
        mailbox = AsyncMock()
        agent = _make_agent(mailbox=mailbox)
        await agent._mark_mail_read(None)
        mailbox.mark_read.assert_not_awaited()

    async def test_process_message_marks_trigger_mail_read(self):
        """The synchronous processing path advances the cursor too."""
        mailbox = AsyncMock()
        mailbox.recent = AsyncMock(return_value=[])
        agent = _make_agent(mailbox=mailbox)
        mail = _mail()

        # Drive one session to completion with a trivially failing LLM
        # (max errors reached -> session completes -> cursor advances)
        agent.llm_client.chat = AsyncMock(side_effect=Exception("down"))

        await agent._process_message(mail)

        mailbox.mark_read.assert_awaited_once_with(mail.id)
