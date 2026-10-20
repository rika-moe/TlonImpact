"""Bounded rendering of member session journal events."""

from typing import Any

#: Truncation budget. The digest must stay small enough to drop into the
#: conversation context without blowing it up: the trigger is clipped to
#: 300 chars, each LLM reply to 600, at most 12 replies are shown, and the
#: whole rendering is hard-capped at 12 000 chars (a marked tail
#: truncation, so a corrupt or adversarial log can never overflow it).
_TRIGGER_MAX = 300
_RESPONSE_MAX = 600
_RESPONSES_LIMIT = 12
_TOTAL_MAX = 12_000


def _clip(text: str, limit: int) -> str:
    """Clip text to ``limit`` chars, marking the truncation."""
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def render_session_digest(events: list[dict[str, Any]]) -> str:
    """Render one session's journal events as a bounded readable digest.

    Contents: the wake trigger text, the end reason, every LLM reply in
    order, and a per-name tool-call count — the same event vocabulary as
    the session-recap renderer, but exhaustive over the session instead of
    a one-session recap.
    """
    trigger = ""
    end_reason = ""
    replies: list[str] = []
    tool_counts: dict[str, int] = {}
    for event in events:
        event_type = event.get("type")
        if event_type == "session/start":
            trigger = str(event.get("trigger_content") or "")
        elif event_type == "llm/response":
            content = (event.get("response") or {}).get("content") or ""
            if content:
                replies.append(str(content))
        elif event_type == "tool/result":
            tool_call = event.get("tool_call") or {}
            name = (tool_call.get("function") or {}).get("name", "unknown")
            tool_counts[name] = tool_counts.get(name, 0) + 1
        elif event_type == "session/end":
            end_reason = str(event.get("reason") or "")

    lines: list[str] = []
    if trigger:
        lines.append(f"触发语：{_clip(trigger, _TRIGGER_MAX)}")
    if end_reason:
        lines.append(f"结束：{end_reason}")
    if tool_counts:
        counts = "、".join(f"{name} ×{n}" for name, n in tool_counts.items())
        lines.append(f"工具调用：{counts}")
    shown = replies[:_RESPONSES_LIMIT]
    for index, reply in enumerate(shown, start=1):
        lines.append(f"第 {index} 轮回复：{_clip(reply, _RESPONSE_MAX)}")
    if len(replies) > len(shown):
        lines.append(f"……（共 {len(replies)} 轮回复，仅显示前 {len(shown)} 轮）")
    if not lines:
        lines.append("（日志中没有可展示的触发或回复内容）")
    return _clip("\n".join(lines), _TOTAL_MAX)
