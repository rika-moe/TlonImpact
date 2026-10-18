"""Render reviewable output without turning tool intentions into execution facts."""

import json

from tlon.agents.session import AgentSession
from tlon.base.llm_client import LLMResponse


def render_review_output(
    session: AgentSession, response: LLMResponse, *, blocked: bool = False
) -> str:
    """Keep complete arguments and classify calls using runtime records only."""
    parts: list[str] = []
    if response.content:
        parts.append(f"[本轮文本输出；不是执行回执]\n{response.content}")
    results = {entry["tool_call"].id: entry["result"] for entry in session.all_tool_results}
    dispatched_ids = session.get_pending_tasks().keys()
    for call in response.tool_calls or []:
        if call.id in results:
            status = "已收集工具回执；执行/投递结果以该回执为准，不能由草稿推断。"
            receipt = json.dumps(results[call.id].to_dict(), ensure_ascii=False)
        elif call.id in dispatched_ids:
            status = "已派发，结果尚未收集；执行/投递结果未知，不能视为未执行或已发送。"
            receipt = None
        else:
            status = ("已被评审拦截；" if blocked else "") + (
                "尚未派发、尚未执行；若为邮件调用，邮件尚未发送。"
            )
            receipt = None
        parts.append(
            f"[调用编号] {call.id}\n[工具] {call.function_name}\n"
            f"[运行时状态] {status}\n"
            f"[完整参数]\n{json.dumps(call.arguments, ensure_ascii=False)}"
        )
        if receipt is not None:
            parts.append(f"[真实工具回执]\n{receipt}")
    return "\n\n".join(parts)
