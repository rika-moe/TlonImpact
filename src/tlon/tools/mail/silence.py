"""Silence tool — politely not reply (an exit from the loop, like sendEmail's
``is_complete=true`` but without sending anything).

The agent decides a mail needs no answer (a detected loop, a duplicate, a
one-way notice) and calls ``silence``: the current session completes, no
mail is sent, and the trigger mail is marked read. Nothing persists and
nothing is suppressed afterwards — the next mail triggers a fresh session
exactly as before. The tool is a completion signal for the act stage:
calling it must be the only tool call in the round (like sendEmail with
``is_complete=true``).
"""

from typing import Any

from tlon.tools.base import Tool, ToolResult


class SilenceTool(Tool):
    """Tool to end the current round without replying.

    Completion-only: the loop finishes, no mail is emitted, and the
    trigger mail is marked read (same cursor path as a completed
    sendEmail round).
    """

    def __init__(self) -> None:
        super().__init__(
            name="silence",
            description=(
                "决定不回复当前邮件时调用：立即结束本轮思考，不发送任何邮件。\n"
                "适用于：检测到邮件循环、重复内容、无需回应的通知等。\n"
                "注意：调用 silence 时本轮只能调用 silence，不能同时调用其他工具。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "可选。不回复的原因（记录在工具结果里，不发送）",
                    },
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        reason = str(kwargs.get("reason") or "").strip()
        return ToolResult(
            success=True,
            data={"silenced": True, "reason": reason},
            message="本轮已结束（不回复当前邮件）" + (f"：{reason}" if reason else ""),
        )
