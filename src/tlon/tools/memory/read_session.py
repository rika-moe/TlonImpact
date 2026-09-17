"""Tool for reading back one session's journal (ADR 0017 stage 4).

The read-back half of the claim model: ``searchMemories`` returns claims
with ``source_session_ids``; this tool lets the agent follow a reference
and read the original session — the claim is the compressed pointer, the
journal is the truth (ReadAgent pattern).
"""

from typing import TYPE_CHECKING, Any

from tlon.base.logger import get_logger
from tlon.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from tlon.memory.manager import SessionLogReader

logger = get_logger(__name__)

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

    Contents: the trigger, the end reason, every LLM reply in order, and
    a per-name tool-call count — the same event vocabulary as the
    thread-recap renderer, but exhaustive over the session instead of a
    one-round recap.
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
        lines.append(f"触发：{_clip(trigger, _TRIGGER_MAX)}")
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


class ReadSessionTool(Tool):
    """Tool to read back one past session's journal as a digest.

    Closes the claim → source loop: ``searchMemories`` exposes each
    claim's ``source_session_ids``; this tool renders that session's
    original trigger and replies from its journal log, so the agent can
    recover detail the claim compressed away.

    Example:
        >>> tool = ReadSessionTool(journal)
        >>> result = await tool.execute(session_id="agent1-<mail-id>")
    """

    def __init__(self, logs: "SessionLogReader"):
        """Initialize with the session journal (SessionLogReader port).

        Args:
            logs: Session log reader — in production the agent's own
                SessionJournal, injected structurally so tlon.tools never
                imports tlon.agents
        """
        super().__init__(
            name="readSession",
            description=(
                "回读一个历史会话的原文摘要（触发来信 + 各轮回复 + 工具活动）。"
                "searchMemories 返回的记忆条带 source_session_ids 来源引用，"
                "需要细节时用本工具按 session id 回读原文"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "要回读的会话 id（searchMemories 结果里的 source_session_ids 之一）",
                    },
                },
                "required": ["session_id"],
            },
        )
        self._logs = logs

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the tool to render one session's journal digest.

        Args:
            session_id: The session whose log to read

        Returns:
            ToolResult with the bounded digest; a missing/empty log is a
            clean error, never an exception
        """
        session_id: str = kwargs["session_id"]

        try:
            events = await self._logs.read_events(session_id)
        except Exception as e:
            logger.error("session_read_failed", session_id=session_id, error=str(e), exc_info=True)
            return ToolResult(
                success=False,
                data={},
                error=str(e),
                message=f"❌ 回读会话失败: {e}",
            )
        if not events:
            return ToolResult(
                success=False,
                data={},
                error=f"session not found: {session_id}",
                message=f"❌ 会话 {session_id} 不存在（无日志）。session id 来自 searchMemories 的 source_session_ids",
            )

        digest = render_session_digest(events)
        return ToolResult(
            success=True,
            data={"session_id": session_id, "digest": digest, "event_count": len(events)},
            message=f"会话 {session_id} 摘要：\n{digest}",
        )
