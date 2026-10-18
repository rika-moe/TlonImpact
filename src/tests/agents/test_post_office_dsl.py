"""DSL mail failure boundaries use a real post office, not only mock actions."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.mail_helpers import make_office
from tlon.agents.member_runtime import MemberRuntime
from tlon.agents.member_spec import MemberSpec, validate_member_spec
from tlon.agents.vocabulary import make_member_builtins
from tlon.base.events.event_bus import EventBus
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mailbox import Mailbox
from tlosure.edn import loads


def runtime_at(tmp_path: Path) -> MemberRuntime:
    bus = EventBus()
    bus.publish = AsyncMock()
    spec = MemberSpec.from_edn(
        loads("""{:version 1 :rules [
      {:name "notify" :on :boot :actions [
        (emit "rejected" ["missing@tlon.internal"])
        (emit "accepted" ["b@tlon.internal"])]}]}""")
    )
    builtins = make_member_builtins()
    validate_member_spec(spec, builtins=builtins)
    return MemberRuntime(
        member_id="a",
        name="A",
        event_bus=bus,
        spec=spec,
        builtins=builtins,
        post_office=make_office(bus, tmp_path, ["a", "b"]),
    )


async def test_rule_rejection_does_not_abort_later_actions(tmp_path: Path) -> None:
    member = runtime_at(tmp_path)
    member._logger = MagicMock()
    await member._run_rule(member.spec.rules[0], trigger_event=None)
    inbox = await Mailbox(TlonPaths(home=tmp_path).mail_database_file(), "b").inbox()
    assert [mail.content for mail in inbox] == ["accepted"]
    rejection = member._logger.warning.call_args
    assert rejection.args[0] == "dsl_mail_rejected"
    assert rejection.kwargs["invalid_recipients"] == ["missing@tlon.internal"]


async def test_dsl_infrastructure_failure_is_observable_not_a_rejection(tmp_path: Path) -> None:
    member = runtime_at(tmp_path)
    assert member._post_office is not None
    member._post_office.post = AsyncMock(side_effect=OSError("disk full"))
    member._logger = MagicMock()
    await member._send_mail(to=["b@tlon.internal"], content="unknown")
    member._logger.warning.assert_not_called()
    unknown = member._logger.error.call_args
    assert unknown.args[0] == "dsl_mail_submission_unknown"
    assert unknown.kwargs["request_id"]
    assert unknown.kwargs["error"] == "disk full"


async def test_dsl_cancellation_is_not_swallowed(tmp_path: Path) -> None:
    import asyncio

    member = runtime_at(tmp_path)
    assert member._post_office is not None
    member._post_office.post = AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await member._send_mail(to=["b@tlon.internal"], content="cancelled")
