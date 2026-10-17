"""Tests for the mail tools (tools/mail/send_email.py, search_mails.py,
check_inbox.py, mark_read.py)."""

import pytest

from tlon.agents.mail_inbox import mark_read_actor, unread_mails_provider
from tlon.agents.mail_search import mailbox_mails_provider
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox
from tlon.tools.mail.check_inbox import (
    CHECK_INBOX_DEFAULT_LIMIT,
    CHECK_INBOX_MAX_LIMIT,
    CheckInboxTool,
)
from tlon.tools.mail.forward_mail import ForwardMailTool
from tlon.tools.mail.mark_read import MarkReadTool
from tlon.tools.mail.search_mails import (
    CONTENT_EXCERPT_LENGTH,
    SEARCH_MAILS_MAX_LIMIT,
    SearchMailsTool,
)
from tlon.tools.mail.send_email import SendEmailTool


class TestSendEmailTool:
    @pytest.mark.asyncio
    async def test_execute_returns_mail_data(self):
        tool = SendEmailTool()
        result = await tool.execute(
            to=["a-001@tlon.internal"],
            content="结论如下",
            is_complete=True,
            cc=["a-002@tlon.internal"],
            subject="汇报",
        )

        assert result.success
        assert result.data["to"] == ["a-001@tlon.internal"]
        assert result.data["cc"] == ["a-002@tlon.internal"]
        assert result.data["subject"] == "汇报"
        assert result.data["content"] == "结论如下"
        assert result.data["is_complete"] is True
        assert result.data["action"] == "send_email"

    @pytest.mark.asyncio
    async def test_defaults(self):
        tool = SendEmailTool()
        result = await tool.execute(to=["b-002@tlon.internal"], content="进度", is_complete=False)

        assert result.success
        assert result.data["cc"] == []
        assert result.data["subject"] is None
        assert result.data["in_reply_to"] is None
        assert result.data["endorsing"] is None
        assert result.data["is_complete"] is False

    @pytest.mark.asyncio
    async def test_endorsing_passes_through(self):
        """The endorsement signal () rides the tool result data to
        the stage, which declares it on the MailEffect."""
        tool = SendEmailTool()
        result = await tool.execute(
            to=["a-001@tlon.internal"],
            content="这封信帮到了我",
            is_complete=True,
            endorsing="m-helpful",
        )

        assert result.success
        assert result.data["endorsing"] == "m-helpful"

    def test_tool_schema(self):
        tool = SendEmailTool()
        assert tool.name == "sendEmail"
        required = tool.parameters["required"]
        assert set(required) == {"to", "content", "is_complete"}


class TestForwardMailTool:
    """forwardMail (ADR 0029): declarative like sendEmail — execute only
    declares the intent; the engine composes and sends the forward."""

    @pytest.mark.asyncio
    async def test_execute_returns_forward_data(self):
        tool = ForwardMailTool()
        result = await tool.execute(
            mail_id="m1",
            to=["a-001@tlon.internal"],
            cc=["a-002@tlon.internal"],
            comment="转给你看看",
        )

        assert result.success
        assert result.data["mail_id"] == "m1"
        assert result.data["to"] == ["a-001@tlon.internal"]
        assert result.data["cc"] == ["a-002@tlon.internal"]
        assert result.data["comment"] == "转给你看看"
        assert result.data["action"] == "forward_mail"

    @pytest.mark.asyncio
    async def test_defaults(self):
        tool = ForwardMailTool()
        result = await tool.execute(mail_id="m1", to=["b-002@tlon.internal"])

        assert result.success
        assert result.data["cc"] == []
        assert result.data["comment"] is None

    @pytest.mark.asyncio
    async def test_message_defers_success_until_post_office_submission(self):
        """Constructing a request does not claim delivery success."""
        tool = ForwardMailTool()
        result = await tool.execute(mail_id="m1", to=["b-002@tlon.internal"])

        assert "邮局提交" in result.message

    def test_tool_schema(self):
        tool = ForwardMailTool()
        assert tool.name == "forwardMail"
        required = tool.parameters["required"]
        # subject / is_complete are not parameters: the subject is
        # generated (Fwd: 原主题) and a forward never ends the loop.
        assert set(required) == {"mail_id", "to"}
        assert "subject" not in tool.parameters["properties"]
        assert "is_complete" not in tool.parameters["properties"]


AGENT_ID = "a-001"
OWN = "a-001@tlon.internal"
BOB = "b-002@tlon.internal"


def _mail(
    mail_id: str,
    from_address: str,
    to: list[str],
    content: str,
    timestamp: str,
    subject: str | None = None,
) -> Mail:
    return Mail(
        id=mail_id,
        from_address=from_address,
        to=to,
        cc=[],
        subject=subject,
        content=content,
        in_reply_to=None,
        thread_id=mail_id,
        forward_of=None,
        timestamp=timestamp,
    )


@pytest.fixture
def tlon_paths(tmp_path) -> TlonPaths:
    return TlonPaths(home=tmp_path)


@pytest.fixture
def mailbox(tlon_paths: TlonPaths) -> Mailbox:
    return Mailbox(tlon_paths.mail_database_file(), AGENT_ID)


def _search_tool(tlon_paths: TlonPaths) -> SearchMailsTool:
    return SearchMailsTool(provider=mailbox_mails_provider(tlon_paths), agent_id=AGENT_ID)


async def _seed_mailbox(mailbox: Mailbox) -> None:
    await mailbox.append_inbox(
        _mail("m1", BOB, [OWN], "请分析一下季度数据", "2026-09-06T10:00:00", subject="季度汇报")
    )
    await mailbox.append_sent(
        _mail(
            "m2",
            OWN,
            [BOB],
            "季度数据分析完成，结论如下",
            "2026-09-06T10:05:00",
            subject="Re: 季度汇报",
        )
    )
    await mailbox.append_inbox(
        _mail("m3", BOB, [OWN], "周报别忘了", "2026-09-06T11:00:00", subject="周报提醒")
    )


class TestSearchMailsTool:
    @pytest.mark.asyncio
    async def test_keyword_and_match(self, tlon_paths: TlonPaths, mailbox: Mailbox):
        await _seed_mailbox(mailbox)
        tool = _search_tool(tlon_paths)

        result = await tool.execute(query="季度 分析")

        assert result.success
        # "季度"+"分析" AND: m1（正文含两者）与 m2（主题/正文各含其一）都命中，m3 不命中
        assert [m["id"] for m in result.data["mails"]] == ["m2", "m1"]  # 新→旧
        assert result.data["mails"][0]["box"] == "sent"
        assert "content" not in result.data["mails"][0]
        assert "content_excerpt" in result.data["mails"][0]

    @pytest.mark.asyncio
    async def test_keyword_matches_address(self, tlon_paths: TlonPaths, mailbox: Mailbox):
        await _seed_mailbox(mailbox)
        tool = _search_tool(tlon_paths)

        result = await tool.execute(query="b-002")

        assert result.success
        assert result.data["count"] == 3

    @pytest.mark.asyncio
    async def test_mail_id_returns_full_content(self, tlon_paths: TlonPaths, mailbox: Mailbox):
        long_content = "长" * (CONTENT_EXCERPT_LENGTH + 100)
        await mailbox.append_inbox(_mail("m-long", BOB, [OWN], long_content, "2026-09-06T12:00:00"))
        tool = _search_tool(tlon_paths)

        result = await tool.execute(mail_id="m-long")

        assert result.success
        assert result.data["mail"]["content"] == long_content

    @pytest.mark.asyncio
    async def test_mail_id_not_found(self, tlon_paths: TlonPaths, mailbox: Mailbox):
        await _seed_mailbox(mailbox)
        tool = _search_tool(tlon_paths)

        result = await tool.execute(mail_id="nope")

        assert not result.success
        assert "nope" in result.error

    @pytest.mark.asyncio
    async def test_limit_truncates(self, tlon_paths: TlonPaths, mailbox: Mailbox):
        for i in range(25):
            await mailbox.append_inbox(
                _mail(f"m{i:02d}", BOB, [OWN], "命中词", f"2026-09-06T10:{i:02d}:00")
            )
        tool = _search_tool(tlon_paths)

        capped = await tool.execute(query="命中词", limit=SEARCH_MAILS_MAX_LIMIT + 5)
        assert capped.success
        assert capped.data["count"] == SEARCH_MAILS_MAX_LIMIT

        defaulted = await tool.execute(query="命中词")
        assert defaulted.data["count"] == 10

        explicit = await tool.execute(query="命中词", limit=3)
        assert [m["id"] for m in explicit.data["mails"]] == ["m24", "m23", "m22"]

    @pytest.mark.asyncio
    async def test_only_own_mailbox(self, tlon_paths: TlonPaths, tmp_path):
        # Bob 的邮箱里有命中邮件，但工具绑定的是 a-001 的邮箱。
        bobs = Mailbox(tlon_paths.mail_database_file(), "b-002")
        await bobs.append_inbox(_mail("b1", OWN, [BOB], "季度数据", "2026-09-06T10:00:00"))
        tool = _search_tool(tlon_paths)

        result = await tool.execute(query="季度")

        assert result.success
        assert result.data["count"] == 0

    @pytest.mark.asyncio
    async def test_empty_result(self, tlon_paths: TlonPaths, mailbox: Mailbox):
        await _seed_mailbox(mailbox)
        tool = _search_tool(tlon_paths)

        result = await tool.execute(query="不存在的关键词")

        assert result.success
        assert result.data["mails"] == []
        assert result.data["count"] == 0

    @pytest.mark.asyncio
    async def test_requires_query_or_mail_id(self, tlon_paths: TlonPaths):
        tool = _search_tool(tlon_paths)

        result = await tool.execute()

        assert not result.success
        assert "至少传一个" in result.error

    @pytest.mark.asyncio
    async def test_external_flag_marks_untrusted_senders(
        self, tlon_paths: TlonPaths, mailbox: Mailbox
    ):
        """searchMails results carry the derived trust flag
        (docs/design/mail.md):
        external senders are marked, internal ones are not."""
        await mailbox.append_inbox(
            _mail("ext1", "notifications@github.com", [OWN], "CI 红了", "2026-09-06T10:00:00")
        )
        await mailbox.append_inbox(_mail("int1", BOB, [OWN], "内部来信", "2026-09-06T11:00:00"))
        tool = _search_tool(tlon_paths)

        external = await tool.execute(query="CI")
        assert external.data["mails"][0]["external"] is True

        internal = await tool.execute(query="内部来信")
        assert internal.data["mails"][0]["external"] is False

        # mail_id lookup returns the full dict, flag included
        full = await tool.execute(mail_id="ext1")
        assert full.data["mail"]["external"] is True

    @pytest.mark.asyncio
    async def test_excerpt_truncated(self, tlon_paths: TlonPaths, mailbox: Mailbox):
        await mailbox.append_inbox(
            _mail(
                "m-long", BOB, [OWN], "长" * (CONTENT_EXCERPT_LENGTH + 100), "2026-09-06T12:00:00"
            )
        )
        tool = _search_tool(tlon_paths)

        result = await tool.execute(query="长")

        excerpt = result.data["mails"][0]["content_excerpt"]
        assert excerpt == "长" * CONTENT_EXCERPT_LENGTH + "…"


def _check_inbox_tool(tlon_paths: TlonPaths) -> CheckInboxTool:
    return CheckInboxTool(provider=unread_mails_provider(tlon_paths), agent_id=AGENT_ID)


def _mark_read_tool(tlon_paths: TlonPaths) -> MarkReadTool:
    return MarkReadTool(actor=mark_read_actor(tlon_paths), agent_id=AGENT_ID)


class TestCheckInboxTool:
    @pytest.mark.asyncio
    async def test_returns_unread_full_content_oldest_first(
        self, tlon_paths: TlonPaths, mailbox: Mailbox
    ):
        # 新→旧 append，返回必须是旧→新（处理顺序）；已发出的不计未读。
        await _seed_mailbox(mailbox)
        tool = _check_inbox_tool(tlon_paths)

        result = await tool.execute()

        assert result.success
        assert [m["id"] for m in result.data["mails"]] == ["m1", "m3"]
        assert result.data["count"] == 2
        assert result.data["total_unread"] == 2
        first = result.data["mails"][0]
        assert first["content"] == "请分析一下季度数据"  # 完整正文，不是节选
        assert first["subject"] == "季度汇报"
        assert first["from_address"] == BOB
        assert first["to"] == [OWN]
        assert first["thread_id"] == "m1"
        assert first["timestamp"] == "2026-09-06T10:00:00"
        assert first["external"] is False

    @pytest.mark.asyncio
    async def test_external_flag_marks_untrusted_senders(
        self, tlon_paths: TlonPaths, mailbox: Mailbox
    ):
        await mailbox.append_inbox(
            _mail("ext1", "notifications@github.com", [OWN], "CI 红了", "2026-09-06T10:00:00")
        )
        tool = _check_inbox_tool(tlon_paths)

        result = await tool.execute()

        assert result.data["mails"][0]["external"] is True

    @pytest.mark.asyncio
    async def test_empty_inbox(self, tlon_paths: TlonPaths):
        tool = _check_inbox_tool(tlon_paths)

        result = await tool.execute()

        assert result.success
        assert result.data["mails"] == []
        assert result.data["total_unread"] == 0
        assert "没有未读" in result.message

    @pytest.mark.asyncio
    async def test_read_mails_excluded(self, tlon_paths: TlonPaths, mailbox: Mailbox):
        await _seed_mailbox(mailbox)
        await mailbox.mark_read("m1")
        tool = _check_inbox_tool(tlon_paths)

        result = await tool.execute()

        assert [m["id"] for m in result.data["mails"]] == ["m3"]
        assert result.data["total_unread"] == 1

    @pytest.mark.asyncio
    async def test_limit_truncates_and_total_unread_survives(
        self, tlon_paths: TlonPaths, mailbox: Mailbox
    ):
        for i in range(CHECK_INBOX_MAX_LIMIT + 10):
            await mailbox.append_inbox(
                _mail(f"m{i:02d}", BOB, [OWN], f"来信 {i}", f"2026-09-06T10:{i:02d}:00")
            )
        tool = _check_inbox_tool(tlon_paths)

        capped = await tool.execute(limit=CHECK_INBOX_MAX_LIMIT + 5)
        assert capped.data["count"] == CHECK_INBOX_MAX_LIMIT
        assert capped.data["total_unread"] == CHECK_INBOX_MAX_LIMIT + 10
        # 截断保留最旧的（处理顺序），不是最新的
        assert capped.data["mails"][0]["id"] == "m00"

        defaulted = await tool.execute()
        assert defaulted.data["count"] == CHECK_INBOX_DEFAULT_LIMIT

        explicit = await tool.execute(limit=3)
        assert [m["id"] for m in explicit.data["mails"]] == ["m00", "m01", "m02"]

    @pytest.mark.asyncio
    async def test_only_own_mailbox(self, tlon_paths: TlonPaths):
        # Bob 的邮箱里有未读，但工具绑定的是 a-001 的邮箱。
        bobs = Mailbox(tlon_paths.mail_database_file(), "b-002")
        await bobs.append_inbox(_mail("b1", OWN, [BOB], "季度数据", "2026-09-06T10:00:00"))
        tool = _check_inbox_tool(tlon_paths)

        result = await tool.execute()

        assert result.success
        assert result.data["total_unread"] == 0


class TestMarkReadTool:
    @pytest.mark.asyncio
    async def test_batch_mark_reduces_unread(self, tlon_paths: TlonPaths, mailbox: Mailbox):
        await _seed_mailbox(mailbox)  # 未读：m1, m3（m2 是 sent）
        tool = _mark_read_tool(tlon_paths)

        result = await tool.execute(mail_ids=["m1", "m3"])

        assert result.success
        assert result.data == {"marked": ["m1", "m3"], "already_read": [], "not_found": []}
        assert await mailbox.unread() == []

    @pytest.mark.asyncio
    async def test_idempotent_repeat_mark(self, tlon_paths: TlonPaths, mailbox: Mailbox):
        await _seed_mailbox(mailbox)
        tool = _mark_read_tool(tlon_paths)

        await tool.execute(mail_ids=["m1"])
        again = await tool.execute(mail_ids=["m1"])

        assert again.success
        assert again.data["marked"] == []
        assert again.data["already_read"] == ["m1"]
        assert [mail.id for mail in await mailbox.unread()] == ["m3"]

    @pytest.mark.asyncio
    async def test_not_found_never_reaches_cursor(self, tlon_paths: TlonPaths, mailbox: Mailbox):
        await _seed_mailbox(mailbox)
        tool = _mark_read_tool(tlon_paths)

        result = await tool.execute(mail_ids=["nope", "m1"])

        assert result.success
        assert result.data["not_found"] == ["nope"]
        assert result.data["marked"] == ["m1"]
        assert [mail.id for mail in await mailbox.unread()] == ["m3"]

    @pytest.mark.asyncio
    async def test_sent_mail_id_is_not_found(self, tlon_paths: TlonPaths, mailbox: Mailbox):
        """已发出的邮件不是收件箱未读，标读它不落 cursor。"""
        await _seed_mailbox(mailbox)  # m2 在 sent
        tool = _mark_read_tool(tlon_paths)

        result = await tool.execute(mail_ids=["m2"])

        assert result.data["not_found"] == ["m2"]
        assert [mail.id for mail in await mailbox.unread()] == ["m1", "m3"]

    @pytest.mark.asyncio
    async def test_requires_mail_ids(self, tlon_paths: TlonPaths):
        tool = _mark_read_tool(tlon_paths)

        missing = await tool.execute()
        assert not missing.success

        empty = await tool.execute(mail_ids=[])
        assert not empty.success

        wrong_type = await tool.execute(mail_ids="m1")
        assert not wrong_type.success

    @pytest.mark.asyncio
    async def test_only_own_mailbox(self, tlon_paths: TlonPaths, mailbox: Mailbox):
        """工具绑定 a-001：标读 a-001 的 id 不影响 Bob 的未读。"""
        bobs = Mailbox(tlon_paths.mail_database_file(), "b-002")
        await bobs.append_inbox(_mail("b1", OWN, [BOB], "给 Bob 的信", "2026-09-06T10:00:00"))
        await mailbox.append_inbox(_mail("m1", BOB, [OWN], "给自己的信", "2026-09-06T10:00:00"))
        tool = _mark_read_tool(tlon_paths)

        # a-001 的收件箱里没有 b1——not_found，且 Bob 的未读不变
        result = await tool.execute(mail_ids=["b1", "m1"])

        assert result.data["not_found"] == ["b1"]
        assert result.data["marked"] == ["m1"]
        assert [m.id for m in await bobs.unread()] == ["b1"]
