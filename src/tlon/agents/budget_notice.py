"""Deterministic resource-limit notices derived only from collected tool results."""

import json
from dataclasses import dataclass

from tlon.agents.session import AgentSession

_DETAIL_LIMIT = 10


@dataclass(frozen=True)
class _Submission:
    request_id: str
    mail_id: str | None
    status: str


def _identifier(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def resource_limit_notice(session: AgentSession, reason: str, hint: str | None = None) -> str:
    """Report confirmed submissions, rejections and uncertain outcomes, never drafts.

    A receipt confirms submission to the post office, not reading or external
    delivery. Request retries converge on the confirmed receipt when available;
    distinct requests returning the same receipt count as one submitted mail.
    """
    attempts: dict[str, _Submission] = {}
    for entry in session.all_tool_results:
        call, result = entry["tool_call"], entry["result"]
        if call.function_name not in ("mail-send", "mail-forward"):
            continue
        data = result.data if isinstance(result.data, dict) else {}
        request_id = (
            _identifier(data.get("request_id"))
            or _identifier(call.arguments.get("request_id"))
            or f"session:{session.id}:{call.id}"
        )
        mail_id = _identifier(data.get("mail_id")) if result.success else None
        status = (
            "confirmed"
            if mail_id
            else "unknown"
            if result.success or data.get("outcome") == "unknown"
            else "rejected"
        )
        previous = attempts.get(request_id)
        # A later rejection cannot disprove an earlier ambiguous submission.
        # Only a durable receipt resolves uncertainty for the same request.
        if previous is None or (
            previous.status != "confirmed"
            and (status != "rejected" or previous.status != "unknown")
        ):
            attempts[request_id] = _Submission(request_id, mail_id, status)

    confirmed = sorted({item.mail_id for item in attempts.values() if item.mail_id})
    rejected = sorted(item.request_id for item in attempts.values() if item.status == "rejected")
    unknown = sorted(item.request_id for item in attempts.values() if item.status == "unknown")
    cause = "累计 token 预算已用尽" if reason == "max_tokens" else "迭代轮数已达上限"
    lines = [
        "[运行时通知：处理达到资源上限]",
        f"停止原因：{cause}（{reason}）。本次任务未完成。",
        "以下仅统计本次会话已收集的邮件工具执行记录；草稿和评审意见不代表已发信。",
        f"已确认提交邮局：{len(confirmed)} 封。",
        f"明确拒绝：{len(rejected)} 个请求。",
        f"结果未知或缺少提交回执：{len(unknown)} 个请求；不能据此认定已发送或未发送。",
    ]
    for label, identifiers in (
        ("已确认提交的 mail_id", confirmed),
        ("明确拒绝的 request_id", rejected),
        ("结果未知的 request_id", unknown),
    ):
        if identifiers:
            details = ", ".join(
                json.dumps(value, ensure_ascii=False) for value in identifiers[:_DETAIL_LIMIT]
            )
            lines.append(f"{label}：{details}")
            if len(identifiers) > _DETAIL_LIMIT:
                lines.append(f"（仅列前 {_DETAIL_LIMIT} 项，共 {len(identifiers)} 项。）")
    lines.extend(
        [
            "邮局提交回执不代表收件人已读，也不保证外部投递完成。",
            "本通知由运行时生成，与上述邮件工具提交记录分开；本通知自身不计入统计。",
        ]
    )
    if hint:
        lines.append(f"附加提示（成员预设文案，不属于执行记录）：{hint}")
    return "\n".join(lines)
