"""Tests for the mail tools (tools/mail/send_email.py)."""

import pytest

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
        result = await tool.execute(
            to=["all@tlon.internal"], content="进度", is_complete=False
        )

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
