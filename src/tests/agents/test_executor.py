"""EffectExecutor.send_mail failure visibility (issue ).

A failed send (bad Mail declaration or a failing MAIL_NEW publish)
must not vanish into a log line: the sending agent gets a
postmaster-style notice in its own inbox (unread), same style as the
delivery bounce. The notice path is fully defensive — it never goes
through send_mail and never raises.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from tlon.agents.edn import loads
from tlon.agents.executor import EffectExecutor
from tlon.agents.member_spec import MemberRule, MemberSpec, validate_member_spec
from tlon.agents.stages.outcome import MailEffect
from tlon.agents.trigger import AgentIdentity
from tlon.base.events.event import EventType
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import POSTMASTER_ADDRESS
from tlon.organization.mail.mailbox import Mailbox

EDN = """\
{:version 1
 :rules [{:name "respond"
          :on :mail-delivered
          :loop {:context {:sections [(persona) (protocol)]}
                 :stages [(think) (act)]
                 :until (or (iteration-limit-reached?) (error-limit-reached?))
                 :max-iterations 10
                 :max-consecutive-errors 3
                 :reflect {:enabled false :interval-minutes 5}}}]}
"""


def _rule() -> MemberRule:
    spec = MemberSpec.from_edn(loads(EDN))
    validate_member_spec(spec)
    return spec.rules[0]


def _make_executor(
    tmp_path: Path, publish: AsyncMock | None = None
) -> tuple[EffectExecutor, Mailbox, AsyncMock]:
    mailbox = Mailbox(TlonPaths(home=tmp_path).member_mail_dir("agent1"))
    publish = publish or AsyncMock()
    executor = EffectExecutor(
        identity=AgentIdentity(id="agent1", name="Agent", address="agent1@tlon.internal"),
        rule=_rule(),
        mailbox=mailbox,
        publish=publish,
        logger=MagicMock(),
    )
    return executor, mailbox, publish


def _effect(**overrides: object) -> MailEffect:
    fields: dict[str, object] = {
        "to": ["user@tlon.internal"],
        "content": "你好",
        "in_reply_to": "m-trigger",
        "thread_id": "t-root",
    }
    fields.update(overrides)
    return MailEffect(**fields)  # type: ignore[arg-type]


class TestSendMailSuccess:
    async def test_valid_effect_publishes_mail_new_and_no_notice(
        self, tmp_path: Path
    ) -> None:
        executor, mailbox, publish = _make_executor(tmp_path)

        await executor.send_mail(_effect())

        publish.assert_awaited_once()
        assert publish.await_args.args[0] is EventType.MAIL_NEW
        assert await mailbox.unread() == []


class TestSendMailFailureNotice:
    async def test_invalid_mail_lands_notice_in_own_inbox(self, tmp_path: Path) -> None:
        """Mail.create rejects the declaration (bad recipient address):
        the sender finds a postmaster notice in its inbox, and one
        MAIL_DELIVERED points the agent at it."""
        executor, mailbox, publish = _make_executor(tmp_path)

        await executor.send_mail(_effect(to=["not-an-address"]))

        unread = await mailbox.unread()
        assert len(unread) == 1
        notice = unread[0]
        assert notice.from_address == POSTMASTER_ADDRESS
        assert notice.to == ["agent1@tlon.internal"]
        assert "not-an-address" in notice.content
        assert notice.in_reply_to == "m-trigger"
        assert notice.thread_id == "t-root"

        publish.assert_awaited_once()
        assert publish.await_args.args[0] is EventType.MAIL_DELIVERED
        assert publish.await_args.kwargs["payload"]["recipient"] == "agent1"
        assert publish.await_args.kwargs["payload"]["mail"].id == notice.id

    async def test_publish_failure_still_lands_notice(self, tmp_path: Path) -> None:
        """The MAIL_NEW publish itself failed (event bus down): the
        notice goes straight to the inbox — never through the publish
        that just failed (its own MAIL_DELIVERED may fail too)."""
        publish = AsyncMock(side_effect=RuntimeError("bus down"))
        executor, mailbox, _ = _make_executor(tmp_path, publish=publish)

        await executor.send_mail(_effect())  # must not raise

        unread = await mailbox.unread()
        assert len(unread) == 1
        assert unread[0].from_address == POSTMASTER_ADDRESS

    async def test_notice_failure_does_not_raise(self, tmp_path: Path) -> None:
        """A failing notice path (mailbox write broken) is logged only —
        send_mail still never raises."""
        executor, mailbox, publish = _make_executor(tmp_path)
        mailbox.append_inbox = AsyncMock(side_effect=OSError("disk full"))  # type: ignore[method-assign]

        await executor.send_mail(_effect(to=["not-an-address"]))  # must not raise

        # The original MAIL_NEW was never published (create failed), and
        # the notice's MAIL_DELIVERED never went out (append failed first).
        publish.assert_not_awaited()
        assert await mailbox.unread() == []
