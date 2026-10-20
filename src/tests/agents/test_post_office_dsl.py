"""DSL mail shares rejection and unknown-result semantics with tool calls."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.mail_helpers import make_office
from tlon.agents.member_runtime import MemberRuntime
from tlon.agents.member_spec import MemberSpec, validate_member_spec
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.operations.schema import UnknownOutcomeError
from tlon.organization.mail.mailbox import Mailbox
from tlosure.edn import loads


def runtime_at(tmp_path: Path, recipient: str = "missing@tlon.internal") -> MemberRuntime:
    bus = EventBus()
    bus.publish = AsyncMock()  # type: ignore[method-assign]
    spec = MemberSpec.from_edn(
        loads(
            """{:version 1 :rules [
      {:name "notify" :on :boot :actions [
        (mail-send {:to ["RECIPIENT"] :content "first"})
        (mail-send {:to ["b@tlon.internal"] :content "second"})]}]}""".replace(
                "RECIPIENT", recipient
            )
        )
    )
    builtins = make_member_builtins()
    validate_member_spec(spec, builtins=builtins)
    paths = TlonPaths(home=tmp_path)
    return MemberRuntime(
        member_id="a",
        name="A",
        event_bus=bus,
        spec=spec,
        builtins=builtins,
        mailbox=Mailbox(paths.mail_database_file(), "a"),
        tlon_paths=paths,
        post_office=make_office(bus, tmp_path, ["a", "b"]),
    )


async def test_known_rule_rejection_is_terminal_and_aborts_later_actions(tmp_path: Path) -> None:
    member = runtime_at(tmp_path)
    member._logger = MagicMock()
    assert await member._run_rule(member.spec.rules[0], None, run_id="rejected")
    inbox = await Mailbox(TlonPaths(home=tmp_path).mail_database_file(), "b").inbox()
    assert inbox == []
    assert member._program_store.pending() == []
    member._logger.warning.assert_called_once()
    assert "missing@tlon.internal" in member._logger.warning.call_args.kwargs["error"]
    # Same rejected run is terminal, rather than continually retried.
    assert await member._run_rule(member.spec.rules[0], None, run_id="rejected")
    member._logger.warning.assert_called_once()


async def test_unknown_after_real_commit_remains_pending_and_recovery_deduplicates(
    tmp_path: Path, monkeypatch
) -> None:
    member = runtime_at(tmp_path, recipient="b@tlon.internal")
    office = member._post_office
    assert office is not None
    original_notify = office._notify

    async def lose_response(*args: object) -> None:
        raise OSError("receipt unavailable after commit")

    monkeypatch.setattr(office, "_notify", lose_response)
    with pytest.raises(UnknownOutcomeError) as unknown:
        await member._run_rule(member.spec.rules[0], None, run_id="recover")
    assert unknown.value.data["request_id"] == "program:recover:0:mail-send"
    assert len(member._program_store.pending()) == 1
    assert len(office.store.mails("b")) == 1
    monkeypatch.setattr(office, "_notify", original_notify)
    restarted = runtime_at(tmp_path, recipient="b@tlon.internal")
    await restarted._restore_programs()
    assert restarted._program_store.pending() == []
    assert [mail.content for mail in office.store.mails("b")] == ["first", "second"]


async def test_dsl_cancellation_propagates_and_preserves_pending_run(
    tmp_path: Path, monkeypatch
) -> None:
    member = runtime_at(tmp_path, recipient="b@tlon.internal")
    assert member._post_office is not None
    monkeypatch.setattr(member._post_office, "send", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await member._run_rule(member.spec.rules[0], None, run_id="cancelled")
    assert len(member._program_store.pending()) == 1
