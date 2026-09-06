"""EffectExecutor.send_mail failure visibility (issue ) and reply
header resolution (issue ).

A failed send (bad Mail declaration, an unresolvable in_reply_to
reference, or a failing MAIL_NEW publish) must not vanish into a log
line: the sending agent gets a postmaster-style notice in its own inbox
(unread), same style as the delivery bounce. The notice path is fully
defensive — it never goes through send_mail and never raises.

An explicit in_reply_to is validated against the agent's own mailbox:
the reply joins the referenced mail's thread, and an id the mailbox
does not hold (typically a hallucinated one) fails the send loudly
instead of shipping a mail with a dangling reference.
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
from tlon.organization.mail.mail import POSTMASTER_ADDRESS, Mail
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


async def _seed_referenced(
    mailbox: Mailbox, mail_id: str = "m-trigger", thread_id: str = "t-root"
) -> Mail:
    """Append the mail an effect's in_reply_to points at to the inbox."""
    mail = Mail.create(
        from_address="user@tlon.internal",
        to=["agent1@tlon.internal"],
        content="来信",
    )
    mail.id = mail_id
    mail.thread_id = thread_id
    await mailbox.append_inbox(mail)
    return mail


class TestSendMailSuccess:
    async def test_valid_effect_publishes_mail_new_and_no_notice(
        self, tmp_path: Path
    ) -> None:
        executor, mailbox, publish = _make_executor(tmp_path)
        referenced = await _seed_referenced(mailbox)

        await executor.send_mail(_effect())

        publish.assert_awaited_once()
        assert publish.await_args.args[0] is EventType.MAIL_NEW
        mail = publish.await_args.kwargs["payload"]
        assert mail.in_reply_to == "m-trigger"
        assert mail.thread_id == "t-root"
        assert await mailbox.unread() == [referenced]


class TestReplyHeaderResolution:
    async def test_thread_follows_the_referenced_mail(self, tmp_path: Path) -> None:
        """Replying to a non-trigger mail: the reply joins *that* mail's
        thread — the stage-declared thread_id is only the trigger-derived
        fallback and gets re-derived here."""
        executor, mailbox, publish = _make_executor(tmp_path)
        await _seed_referenced(mailbox, mail_id="m-earlier", thread_id="t-earlier-root")

        await executor.send_mail(_effect(in_reply_to="m-earlier"))

        publish.assert_awaited_once()
        assert publish.await_args.args[0] is EventType.MAIL_NEW
        mail = publish.await_args.kwargs["payload"]
        assert mail.in_reply_to == "m-earlier"
        assert mail.thread_id == "t-earlier-root"

    async def test_unknown_reference_fails_with_notice(self, tmp_path: Path) -> None:
        """A hallucinated in_reply_to: no mail ships with a dangling
        reference — the send fails and the agent gets the usual
        postmaster-style notice naming the unknown id."""
        executor, mailbox, publish = _make_executor(tmp_path)

        await executor.send_mail(_effect())  # mailbox holds no m-trigger

        publish.assert_awaited_once()
        assert publish.await_args.args[0] is EventType.MAIL_DELIVERED
        unread = await mailbox.unread()
        assert len(unread) == 1
        notice = unread[0]
        assert notice.from_address == POSTMASTER_ADDRESS
        assert "m-trigger" in notice.content

    async def test_no_mailbox_passes_declared_headers_through(
        self, tmp_path: Path
    ) -> None:
        """Bare setups without a mailbox cannot resolve references: the
        stage-declared headers go out as-is."""
        publish = AsyncMock()
        executor = EffectExecutor(
            identity=AgentIdentity(id="agent1", name="Agent", address="agent1@tlon.internal"),
            rule=_rule(),
            mailbox=None,
            publish=publish,
            logger=MagicMock(),
        )

        await executor.send_mail(_effect())

        publish.assert_awaited_once()
        assert publish.await_args.args[0] is EventType.MAIL_NEW
        mail = publish.await_args.kwargs["payload"]
        assert mail.in_reply_to == "m-trigger"
        assert mail.thread_id == "t-root"


class TestSendMailFailureNotice:
    async def test_invalid_mail_lands_notice_in_own_inbox(self, tmp_path: Path) -> None:
        """Mail.create rejects the declaration (bad recipient address):
        the sender finds a postmaster notice in its inbox, and one
        MAIL_DELIVERED points the agent at it."""
        executor, mailbox, publish = _make_executor(tmp_path)
        await _seed_referenced(mailbox)

        await executor.send_mail(_effect(to=["not-an-address"]))

        unread = await mailbox.unread()
        assert len(unread) == 2  # the seeded reference + the notice
        notice = unread[1]
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
        await _seed_referenced(mailbox)

        await executor.send_mail(_effect())  # must not raise

        unread = await mailbox.unread()
        assert len(unread) == 2  # the seeded reference + the notice
        assert unread[1].from_address == POSTMASTER_ADDRESS

    async def test_notice_failure_does_not_raise(self, tmp_path: Path) -> None:
        """A failing notice path (mailbox write broken) is logged only —
        send_mail still never raises."""
        executor, mailbox, publish = _make_executor(tmp_path)
        await _seed_referenced(mailbox)
        mailbox.append_inbox = AsyncMock(side_effect=OSError("disk full"))  # type: ignore[method-assign]

        await executor.send_mail(_effect(to=["not-an-address"]))  # must not raise

        # The original MAIL_NEW was never published (create failed), and
        # the notice's MAIL_DELIVERED never went out (append failed first).
        publish.assert_not_awaited()
        assert [m.id for m in await mailbox.unread()] == ["m-trigger"]


class TestEndorsementValidation:
    """The endorsing reference gets the same mailbox validation as
    in_reply_to (issue ): hallucinated ids and self-endorsement
    fail the send with a postmaster notice."""

    async def _seed_endorsed(self, mailbox: Mailbox, mail_id: str = "m-helpful") -> Mail:
        """Append the mail being endorsed (from someone else) to the inbox."""
        mail = Mail.create(
            from_address="user@tlon.internal",
            to=["agent1@tlon.internal"],
            content="有用的来信",
        )
        mail.id = mail_id
        await mailbox.append_inbox(mail)
        return mail

    async def test_valid_endorsement_passes_through(self, tmp_path: Path) -> None:
        executor, mailbox, publish = _make_executor(tmp_path)
        await self._seed_endorsed(mailbox)

        await executor.send_mail(
            _effect(in_reply_to=None, thread_id=None, endorsing="m-helpful")
        )

        publish.assert_awaited_once()
        assert publish.await_args.args[0] is EventType.MAIL_NEW
        mail = publish.await_args.kwargs["payload"]
        assert mail.endorsing == "m-helpful"

    async def test_hallucinated_endorsing_fails_with_notice(
        self, tmp_path: Path
    ) -> None:
        executor, mailbox, publish = _make_executor(tmp_path)

        await executor.send_mail(
            _effect(in_reply_to=None, thread_id=None, endorsing="m-ghost")
        )

        publish.assert_awaited_once()
        assert publish.await_args.args[0] is EventType.MAIL_DELIVERED
        unread = await mailbox.unread()
        assert len(unread) == 1
        notice = unread[0]
        assert notice.from_address == POSTMASTER_ADDRESS
        assert "m-ghost" in notice.content

    async def test_self_endorsement_fails_with_notice(self, tmp_path: Path) -> None:
        """Endorsing one's own mail is reputation self-puffery: rejected."""
        executor, mailbox, publish = _make_executor(tmp_path)
        own = Mail.create(
            from_address="agent1@tlon.internal",
            to=["user@tlon.internal"],
            content="我自己写的",
        )
        await mailbox.append_sent(own)

        await executor.send_mail(
            _effect(in_reply_to=None, thread_id=None, endorsing=own.id)
        )

        publish.assert_awaited_once()
        assert publish.await_args.args[0] is EventType.MAIL_DELIVERED
        unread = await mailbox.unread()
        assert len(unread) == 1
        assert unread[0].from_address == POSTMASTER_ADDRESS
        assert own.id in unread[0].content
