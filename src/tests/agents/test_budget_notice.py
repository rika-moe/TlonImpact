"""Resource notices report receipts, never model narratives or proposed mail."""

from tlon.agents.budget_notice import resource_limit_notice
from tlon.agents.session import AgentSession
from tlon.base.llm_client import ToolCall
from tlon.tools.base import ToolResult


def _record(
    session: AgentSession,
    call_id: str,
    request_id: str,
    result: ToolResult,
    name: str = "sendEmail",
) -> None:
    session.add_tool_result(
        ToolCall(id=call_id, function_name=name, arguments={"request_id": request_id}), result
    )


def test_receipts_rejections_unknown_and_no_receipt_are_distinct() -> None:
    session = AgentSession(session_id="s1", trigger_content="已发出所有草稿")
    session.context = [{"role": "assistant", "content": "所有邮件已发送成功"}]
    _record(session, "a", "r1", ToolResult(True, {"mail_id": "m1", "content": "秘密正文"}))
    _record(session, "b", "r2", ToolResult(False, {}, error="退信正文不应泄露"))
    _record(session, "c", "r3", ToolResult(False, {"outcome": "unknown"}))
    _record(session, "d", "r4", ToolResult(True, {"to": ["peer@tlon.internal"]}))
    _record(session, "e", "r5", ToolResult(True, {"mail_id": "not-mail"}), name="searchMails")
    content = resource_limit_notice(session, "max_iterations")
    assert "已确认提交邮局：1 封。" in content
    assert "明确拒绝：1 个请求。" in content
    assert "结果未知或缺少提交回执：2 个请求" in content
    assert 'mail_id："m1"' in content
    assert 'request_id："r2"' in content
    assert 'request_id："r3", "r4"' in content
    for excluded in ("秘密正文", "退信正文", "所有邮件", "not-mail"):
        assert excluded not in content
    assert "本通知自身不计入统计" in content


def test_retries_converge_to_receipt_and_deduplicate_mail_ids() -> None:
    session = AgentSession(session_id="s1", trigger_content="hi")
    _record(session, "a", "r1", ToolResult(False, {"outcome": "unknown"}))
    _record(session, "b", "r1", ToolResult(True, {"mail_id": "m1"}))
    _record(session, "c", "r1", ToolResult(False, {"outcome": "unknown"}))
    _record(session, "d", "r2", ToolResult(True, {"mail_id": "m1"}), name="forwardMail")
    _record(session, "e", "r3", ToolResult(False, {}))
    _record(session, "f", "r3", ToolResult(False, {}))
    content = resource_limit_notice(session, "max_iterations")
    assert "已确认提交邮局：1 封。" in content
    assert "明确拒绝：1 个请求。" in content
    assert "结果未知或缺少提交回执：0 个请求" in content
    assert content.count('"m1"') == 1


def test_no_tool_receipt_means_zero_confirmed_submissions() -> None:
    session = AgentSession(session_id="s1", trigger_content="hi")
    session.context = [{"role": "assistant", "content": "已发前函"}]
    session.loop_scratch = {"critique_feedback": "你上一封信有错"}
    content = resource_limit_notice(session, "max_tokens")
    assert "已确认提交邮局：0 封。" in content
    assert "前函" not in content
    assert "上一封" not in content


def test_details_are_bounded_but_counts_remain_complete() -> None:
    session = AgentSession(session_id="s1", trigger_content="hi")
    for index in range(12):
        _record(session, str(index), f"r{index}", ToolResult(True, {"mail_id": f"m{index}"}))
    content = resource_limit_notice(session, "max_iterations")
    assert "已确认提交邮局：12 封。" in content
    assert "仅列前 10 项，共 12 项" in content


def test_rejection_after_unknown_does_not_prove_earlier_attempt_was_not_submitted() -> None:
    session = AgentSession(session_id="s1", trigger_content="hi")
    _record(session, "a", "r1", ToolResult(False, {"outcome": "unknown"}))
    _record(session, "b", "r1", ToolResult(False, {}))
    content = resource_limit_notice(session, "max_iterations")
    assert "明确拒绝：0 个请求。" in content
    assert "结果未知或缺少提交回执：1 个请求" in content
