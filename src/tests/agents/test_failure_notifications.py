"""Courtesy recovery reads successful facts and paces unresolved work."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.agents.test_module_programs import _member
from tlon.agents.member_runtime import MemberRuntime
from tlon.agents.program_store import ProgramRun, ProgramStore
from tlon.base.paths import TlonPaths
from tlon.operations.invocation import Invocation, InvocationOutcome
from tlon.operations.schema import RecoveryMismatchError, UnknownOutcomeError, edn_value
from tlon.organization.mail.mail import Mail, address_for_member
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.mail.store import MailStore
from tlosure.edn import dumps


def failed(member: MemberRuntime, home: Path) -> tuple[ProgramRun, Mail]:
    store = MailStore(TlonPaths(home=home).mail_database_file())
    store.register_member("human:alice")
    mail = Mail.create(
        from_address=address_for_member("human:alice"),
        to=[address_for_member("member")],
        content="original",
    )
    store.add_to_inbox(mail, "member")
    member._program_store.ensure_entry(
        "failed-run",
        "{}",
        dumps(edn_value({"payload": {"mail": {"__tlon_mail__": mail.to_dict()}}})),
        {},
    )
    member._program_store.fail("failed-run", "failure retained")
    record = member._program_store.get_run("failed-run")
    assert record is not None
    return record, mail


async def test_successful_sqlite_receipt_survives_build_change_without_executor(
    tmp_path: Path,
) -> None:
    member = _member(tmp_path)
    record, original = failed(member, tmp_path)
    member._program_store.build_id = "old-build"
    await member._notify_entry_failure(record)
    assert (
        len(await Mailbox(TlonPaths(home=tmp_path).mail_database_file(), "human:alice").unread())
        == 1
    )
    database = member._program_store.database
    assert database is not None
    before = database.read_bytes()
    restored = _member(tmp_path)
    restored._program_store.build_id = "new-build"
    restored._operation_executor.call = AsyncMock(
        side_effect=AssertionError("must read existing success")
    )
    await restored._restore_entry_failures()
    await restored._restore_entry_failures()
    restored._operation_executor.call.assert_not_awaited()
    assert database.read_bytes() == before
    inbox = await Mailbox(TlonPaths(home=tmp_path).mail_database_file(), "human:alice").unread()
    assert len(inbox) == 1 and inbox[0].in_reply_to == original.id


async def test_old_root_without_receipt_blocks_once_and_never_sends(tmp_path: Path) -> None:
    member = _member(tmp_path)
    record, _ = failed(member, tmp_path)
    member._program_store.build_id = "old"
    member._program_store.ensure_root(record.run_id + ":failure-notification")
    member._program_store.build_id = "new"
    member._logger = MagicMock()
    await member._restore_entry_failures()
    await member._restore_entry_failures()
    assert member._logger.warning.call_count == 1
    assert member._failure_notification_retry[record.run_id].blocked
    assert (
        await Mailbox(TlonPaths(home=tmp_path).mail_database_file(), "human:alice").unread() == []
    )


async def test_temporary_lookup_failure_backs_off_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    member = _member(tmp_path)
    record, _ = failed(member, tmp_path)
    clock = [0.0]
    monkeypatch.setattr("tlon.base.maintenance.monotonic", lambda: clock[0])
    attempts = [0]

    def known_humans() -> list[str]:
        attempts[0] += 1
        if attempts[0] == 1:
            raise OSError("temporary")
        return ["human:alice"]

    lookup = MagicMock(side_effect=known_humans)
    monkeypatch.setattr(MailStore, "known_human_ids", lookup)
    await member._notify_entry_failure(record)
    await member._notify_entry_failure(record)
    assert lookup.call_count == 1
    clock[0] = 31
    await member._notify_entry_failure(record)
    assert lookup.call_count >= 2
    assert record.run_id in member._failure_notification_done
    assert (
        len(await Mailbox(TlonPaths(home=tmp_path).mail_database_file(), "human:alice").unread())
        == 1
    )


async def test_unknown_effect_is_blocked_without_repeating_send(tmp_path: Path) -> None:
    member = _member(tmp_path)
    record, _ = failed(member, tmp_path)
    member._operation_executor.call = AsyncMock(side_effect=UnknownOutcomeError("unknown", {}))
    member._logger = MagicMock()
    await member._notify_entry_failure(record)
    await member._notify_entry_failure(record)
    member._operation_executor.call.assert_awaited_once()
    assert member._logger.warning.call_count == 1
    assert member._failure_notification_retry[record.run_id].blocked


@pytest.mark.parametrize("disk", [False, True])
def test_exact_committed_leaf_read_is_readonly_across_builds(tmp_path: Path, disk: bool) -> None:
    database = tmp_path / "facts.sqlite3" if disk else None
    store = ProgramStore(database, build_id="old")
    args = {"request_id": "root", "to": ["person"], "in_reply_to": "original"}
    invocation = Invocation(
        "call", "root", ("leaf",), "mail-send", json.dumps(args), "old-definition", "old"
    )
    store.ensure_root("root")
    store.commit_invocation(invocation, InvocationOutcome(True, {"mail_id": "receipt"}))
    before = database.read_bytes() if database else None
    store.build_id = "new"
    assert store.read_invocation_fact("root", ("leaf",)) == (
        invocation,
        InvocationOutcome(True, {"mail_id": "receipt"}),
    )
    assert store.read_invocation_fact("root", ("other",)) is None
    with pytest.raises(RecoveryMismatchError):
        store.ensure_root("root")
    if database:
        assert database.read_bytes() == before


@pytest.mark.parametrize("committed_unknown", [False, True])
async def test_persisted_unknown_blocks_before_executor(
    tmp_path: Path, committed_unknown: bool
) -> None:
    member = _member(tmp_path)
    record, mail = failed(member, tmp_path)
    root = record.run_id + ":failure-notification"
    arguments = {
        "to": [mail.from_address],
        "content": "处理未能完成，工作程序已停止。原始来信与执行记录已保留，请检查后重试。",
        "subject": "运行时通知：工作程序停止",
        "in_reply_to": mail.id,
        "request_id": root,
    }
    invocation = Invocation(
        "unknown",
        root,
        ("failure-mail",),
        "mail-send",
        json.dumps(arguments),
        "old-definition",
        member._program_store.build_id,
    )
    member._program_store.ensure_root(root)
    member._program_store.begin_invocation(invocation)
    if committed_unknown:
        member._program_store.commit_invocation(
            invocation,
            InvocationOutcome(False, error_type="UnknownOutcomeError", error_message="pending"),
        )
    member._operation_executor.call = AsyncMock(
        side_effect=AssertionError("must not re-dispatch unknown")
    )
    await member._notify_entry_failure(record)
    await member._notify_entry_failure(record)
    member._operation_executor.call.assert_not_awaited()
    assert member._failure_notification_retry[record.run_id].blocked


@pytest.mark.parametrize("mismatch", ["arguments", "mail"])
async def test_unrelated_success_cannot_suppress_notification(
    tmp_path: Path, mismatch: str
) -> None:
    member = _member(tmp_path)
    record, mail = failed(member, tmp_path)
    root = record.run_id + ":failure-notification"
    arguments = {
        "to": ["different@tlon.internal"] if mismatch == "arguments" else [mail.from_address],
        "content": "处理未能完成，工作程序已停止。原始来信与执行记录已保留，请检查后重试。",
        "subject": "运行时通知：工作程序停止",
        "in_reply_to": mail.id,
        "request_id": root,
    }
    invocation = Invocation(
        "unrelated",
        root,
        ("failure-mail",),
        "mail-send",
        json.dumps(arguments),
        "old-definition",
        member._program_store.build_id,
    )
    member._program_store.ensure_root(root)
    member._program_store.commit_invocation(
        invocation,
        InvocationOutcome(True, {"status": "accepted", "request_id": root, "mail_id": mail.id}),
    )
    member._operation_executor.call = AsyncMock(
        side_effect=AssertionError("mismatch cannot authorize execution")
    )
    await member._notify_entry_failure(record)
    await member._notify_entry_failure(record)
    member._operation_executor.call.assert_not_awaited()
    assert record.run_id not in member._failure_notification_done
    assert member._failure_notification_retry[record.run_id].blocked
