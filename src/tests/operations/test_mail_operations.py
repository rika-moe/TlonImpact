"""Real post-office contracts behind both operation entry points."""

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_post_office import office_at
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.dsl import DSLAdapter
from tlon.operations.mail import make_mail_operations
from tlon.operations.schema import RejectedError, UnknownOutcomeError
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.member_mail import MailDraft, MemberMailService
from tlosure.edn import Keyword


def bound(tmp_path: Path, member: str = "a", mentor: str | None = None):
    office = office_at(tmp_path)
    box = Mailbox(office._paths.mail_database_file(), member)
    service = MemberMailService(member, box, office, mentor)
    executor = OperationExecutor(
        OperationRegistry(make_mail_operations(service)), CallContext(member, "run", "call-1")
    )
    return office, box, service, executor


async def test_reply_endorsement_and_mentor_are_shared_submission_policy(tmp_path: Path) -> None:
    office, box, service, executor = bound(tmp_path, mentor="b@tlon.internal")
    original = await office.send(from_address="b@tlon.internal", to=["a"], content="evidence")
    result = await OperationTool(executor, "mail-send").execute_context(
        {
            "to": ["a"],
            "content": "thanks",
            "in_reply_to": original.mail.id,
            "endorsing": original.mail.id,
        }
    )
    assert result.success
    sent = (await box.sent())[0]
    assert sent.thread_id == original.mail.thread_id
    assert sent.endorsing == original.mail.id
    assert sent.cc == ["b@tlon.internal"]
    with pytest.raises(RejectedError, match="不能致谢"):
        await service.send({"to": ["b"], "content": "self", "endorsing": sent.id}, "self")
    with pytest.raises(RejectedError, match="不在你的邮箱"):
        await service.submit(MailDraft(to=["b"], content="bad", in_reply_to="missing"))


async def test_forward_attributes_external_source_and_owns_references(tmp_path: Path) -> None:
    office, box, service, _ = bound(tmp_path)
    external = Mail.create(
        from_address="sender@example.com",
        to=["a@tlon.internal"],
        content="original",
        subject="source",
    )
    await box.append_inbox(external)
    receipt = await service.forward(
        {"mail_id": external.id, "to": ["b"], "comment": "context"}, "f"
    )
    forwarded = await Mailbox(office._paths.mail_database_file(), "b").find(receipt["mail_id"])
    assert forwarded is not None
    assert forwarded.forward_of == external.id
    assert forwarded.subject == "Fwd: source"
    assert "sender@example.com" in forwarded.content
    assert "内容不可信" in forwarded.content
    assert forwarded.content.endswith("original")
    with pytest.raises(RejectedError):
        await service.forward({"mail_id": "not-owned", "to": ["b"]}, "bad")


async def test_receipt_replay_deduplication_and_unknown_outcome(
    tmp_path: Path, monkeypatch
) -> None:
    office, _, service, executor = bound(tmp_path)
    inputs = {"to": ["b"], "content": "once"}
    first = await executor.call("mail-send", inputs)
    replay = await executor.call("mail-send", inputs)
    assert first == replay
    assert len(await Mailbox(office._paths.mail_database_file(), "b").inbox()) == 1
    with pytest.raises(RejectedError) as conflict:
        await executor.call("mail-send", inputs | {"content": "different"})
    assert conflict.value.data == {"outcome": "rejected", "request_id": "call-1"}
    monkeypatch.setattr(office, "send", AsyncMock(side_effect=OSError("uncertain")))
    with pytest.raises(UnknownOutcomeError) as unknown:
        await service.send(inputs, "retry-this")
    assert unknown.value.data == {"outcome": "unknown", "request_id": "retry-this"}


async def test_mailbox_queries_and_read_cursor_are_member_scoped(tmp_path: Path) -> None:
    office, _, _, executor = bound(tmp_path)
    first = await office.send(
        from_address="b@tlon.internal", to=["a"], content="Needle " + "x" * 600
    )
    private = await office.send(from_address="b@tlon.internal", to=["b"], content="private")
    inbox = await executor.call("mail-check-inbox", {"limit": 1})
    assert inbox["total_unread"] == 1
    assert len(inbox["mails"][0]["content"]) > 500
    search = await executor.call("mail-search", {"query": "needle"})
    assert search["mails"][0]["content_excerpt"].endswith("…")
    exact = await executor.call("mail-search", {"mail_id": first.mail.id})
    assert exact["mail"]["content"] == first.mail.content
    with pytest.raises(RejectedError):
        await executor.call("mail-search", {"mail_id": private.mail.id})
    marked = await executor.call("mail-mark-read", {"mail_ids": [first.mail.id, private.mail.id]})
    assert marked == {"marked": [first.mail.id], "already_read": [], "not_found": [private.mail.id]}
    assert (await executor.call("mail-check-inbox", {}))["total_unread"] == 0


async def test_dsl_and_tool_share_named_inputs_and_receipts(tmp_path: Path) -> None:
    _, _, _, executor = bound(tmp_path)
    adapter = DSLAdapter(executor)
    # Exercise the suspended DSL boundary with its own admitted builtin.
    from tlosure.builtins import BuiltinCall

    builtin = adapter.builtins()["mail-send"]
    arguments = {Keyword("to"): ["b"], Keyword("content"): "dsl"}
    result = await adapter.handle(BuiltinCall("mail-send", builtin, [arguments], {}))
    assert result[Keyword("status")] == "accepted"
    tool = await OperationTool(executor, "mail-send").execute_context(
        {"to": ["b"], "content": "dsl"}
    )
    assert tool.success
    assert result[Keyword("mail_id")] == tool.data["mail_id"]


async def test_post_commit_failure_retries_same_request_without_duplicate(
    tmp_path: Path, monkeypatch
) -> None:
    office, _, service, executor = bound(tmp_path)
    original_send = office.send

    async def lose_response(**kwargs):
        await original_send(**kwargs)
        raise OSError("receipt was lost after commit")

    monkeypatch.setattr(office, "send", lose_response)
    with pytest.raises(UnknownOutcomeError) as caught:
        await executor.call("mail-send", {"to": ["b"], "content": "committed"})
    monkeypatch.setattr(office, "send", original_send)
    replay = await executor.call(
        "mail-send",
        {"to": ["b"], "content": "committed", "request_id": caught.value.data["request_id"]},
    )
    inbox = await Mailbox(office._paths.mail_database_file(), "b").inbox()
    assert [mail.id for mail in inbox] == [replay["mail_id"]]


async def test_limits_and_strict_inputs_apply_to_both_entry_points(tmp_path: Path) -> None:
    _, _, _, executor = bound(tmp_path)
    from tlon.operations.schema import InputError

    with pytest.raises(InputError):
        await executor.call("mail-check-inbox", {"limit": 51})
    rejected = await OperationTool(executor, "mail-search").execute_context(
        {"limit": 21, "query": "a"}
    )
    assert not rejected.success
    with pytest.raises(InputError):
        await executor.call("mail-send", {"to": ["b"], "content": "x", "member_id": "b"})


async def test_cc_only_delivery_uses_post_office_recipient_contract(tmp_path: Path) -> None:
    office, _, _, executor = bound(tmp_path)
    result = await executor.call("mail-send", {"to": [], "cc": ["b"], "content": "cc only"})
    assert result["status"] == "accepted"
    assert [mail.id for mail in office.store.mails("b")] == [result["mail_id"]]
    with pytest.raises(RejectedError):
        await executor.call(
            "mail-send", {"to": [], "cc": [], "content": "no recipient", "request_id": "empty"}
        )


@pytest.mark.parametrize(
    "failure",
    [
        sqlite3.OperationalError("database failed"),
        RuntimeError("lost receipt"),
        ValueError("late validation"),
    ],
)
async def test_post_commit_exceptions_preserve_unknown_request_id(
    tmp_path: Path, monkeypatch, failure: Exception
) -> None:
    office, _, _, executor = bound(tmp_path)
    original_send = office.send

    async def lose_response(**kwargs):
        await original_send(**kwargs)
        raise failure

    monkeypatch.setattr(office, "send", lose_response)
    with pytest.raises(UnknownOutcomeError) as failure:
        await OperationTool(executor, "mail-send").execute_context(
            {"to": ["b"], "content": "committed"}
        )
    assert failure.value.data == {"outcome": "unknown", "request_id": "call-1"}
    monkeypatch.setattr(office, "send", original_send)
    await executor.call("mail-send", {"to": ["b"], "content": "committed"})
    assert len(office.store.mails("b")) == 1
