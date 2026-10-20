"""Automatic engine mail uses the same member-bound domain submission policy."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.mail_helpers import make_office
from tlon.agents.executor import EffectExecutor
from tlon.agents.trigger import AgentIdentity
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.operations.schema import RejectedError, UnknownOutcomeError
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.member_mail import MailDraft
from tlon.organization.mail.post_office import PostOffice


def executor_at(
    tmp_path: Path, *, bound: bool = True
) -> tuple[EffectExecutor, Mailbox, PostOffice]:
    bus = EventBus()
    bus.publish = AsyncMock()  # type: ignore[method-assign]
    office = make_office(bus, tmp_path, ["agent1", "user", "mentor"])
    mailbox = Mailbox(TlonPaths(home=tmp_path).mail_database_file(), "agent1")
    executor = EffectExecutor(
        identity=AgentIdentity("agent1", "Agent", "agent1@tlon.internal"),
        mailbox=mailbox if bound else None,
        publish=bus.publish,
        post_office=office,
        logger=MagicMock(),
        mentor_cc="mentor@tlon.internal",
    )
    return executor, mailbox, office


async def test_automatic_reply_uses_mailbox_thread_and_mentor_policy(tmp_path: Path) -> None:
    executor, mailbox, office = executor_at(tmp_path)
    source = await office.send(from_address="user@tlon.internal", to=["agent1"], content="question")
    receipt = await executor.send_mail(
        MailDraft(
            to=["user"],
            content="answer",
            in_reply_to=source.mail.id,
            thread_id="untrusted",
            endorsing=source.mail.id,
        )
    )
    assert receipt.mail.thread_id == source.mail.thread_id
    assert receipt.mail.endorsing == source.mail.id
    assert receipt.mail.cc == ["mentor@tlon.internal"]
    assert [mail.id for mail in await mailbox.unread()] == [source.mail.id]


@pytest.mark.parametrize("reference", ["in_reply_to", "forward_of", "endorsing"])
async def test_invalid_reference_rejects_without_delivery_or_bounce(
    tmp_path: Path, reference: str
) -> None:
    executor, mailbox, office = executor_at(tmp_path)
    draft = MailDraft(to=["user"], content="intent")
    setattr(draft, reference, "missing")
    with pytest.raises(RejectedError, match="missing"):
        await executor.send_mail(draft)
    assert await mailbox.inbox() == []
    assert office.store.mails("user") == []


@pytest.mark.parametrize("reference", ["in_reply_to", "forward_of"])
async def test_unbound_executor_cannot_bypass_source_validation(
    tmp_path: Path, reference: str
) -> None:
    executor, _, office = executor_at(tmp_path, bound=False)
    draft = MailDraft(to=["user"], content="intent")
    setattr(draft, reference, "missing")
    with pytest.raises(RuntimeError, match="no mail service"):
        await executor.send_mail(draft)
    assert office.store.mails("user") == []


async def test_automatic_forward_carries_source_and_has_new_thread(tmp_path: Path) -> None:
    executor, _, office = executor_at(tmp_path)
    source = await office.send(from_address="user@tlon.internal", to=["agent1"], content="original")
    receipt = await executor.send_mail(
        MailDraft(to=["user"], content="", forward_of=source.mail.id, forward_comment="see this")
    )
    assert receipt.mail.content.startswith("see this\n\n—— 转发的邮件 ——")
    assert receipt.mail.content.endswith("original")
    assert receipt.mail.thread_id == receipt.mail.id
    assert receipt.mail.forward_of == source.mail.id
    assert receipt.mail.cc == ["mentor@tlon.internal"]


async def test_automatic_submission_failure_propagates(tmp_path: Path, monkeypatch) -> None:
    executor, mailbox, office = executor_at(tmp_path)
    monkeypatch.setattr(office, "send", AsyncMock(side_effect=OSError("disk full")))
    with pytest.raises(UnknownOutcomeError, match="disk full"):
        await executor.send_mail(MailDraft(to=["user"], content="intent"))
    assert await mailbox.inbox() == []
