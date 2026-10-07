"""Search mails tool — structured read-only query over the agent's own mailbox ().

The accountability gap: an agent asked "who told me to do this / what did
I reply" previously had no structured way to search its mailbox — only
the passive ``(mail-history)`` context section or raw JSONL reads. This
tool is the mail evidence source of the built-in ``explain-decision``
skill (``tlon/skills/explain-decision/``).
"""

from typing import Any

from tlon.tools.base import Tool, ToolResult
from tlon.tools.mail.ports import MailboxMailsProvider

#: Result-size contract: keyword search returns at most this many mails.
SEARCH_MAILS_DEFAULT_LIMIT = 10
SEARCH_MAILS_MAX_LIMIT = 20

#: Keyword-search hits carry an excerpt, not the full body (payload guard);
#: an exact ``mail_id`` lookup returns the full content.
CONTENT_EXCERPT_LENGTH = 500


def _matches(mail: dict[str, Any], terms: list[str]) -> bool:
    """All terms must appear (AND) across subject, content and addresses."""
    haystack = " ".join(
        [
            str(mail.get("subject") or ""),
            str(mail.get("content") or ""),
            str(mail.get("from_address") or ""),
            *[str(a) for a in mail.get("to") or []],
            *[str(a) for a in mail.get("cc") or []],
        ]
    ).lower()
    return all(term in haystack for term in terms)


def _summary(mail: dict[str, Any]) -> dict[str, Any]:
    """Compact per-mail view for keyword search (content is excerpted)."""
    content = str(mail.get("content") or "")
    excerpt = content[:CONTENT_EXCERPT_LENGTH]
    if len(content) > CONTENT_EXCERPT_LENGTH:
        excerpt += "…"
    return {
        "id": mail.get("id"),
        "box": mail.get("box"),
        "from_address": mail.get("from_address"),
        "external": bool(mail.get("external")),
        "to": mail.get("to") or [],
        "cc": mail.get("cc") or [],
        "subject": mail.get("subject"),
        "timestamp": mail.get("timestamp"),
        "thread_id": mail.get("thread_id"),
        "content_excerpt": excerpt,
    }


class SearchMailsTool(Tool):
    """Tool to search the agent's own mailbox (inbox + sent), read-only.

    Two modes: a keyword search (space-separated terms, AND-matched over
    subject + content + from/to/cc) returning excerpted hits newest first,
    or an exact ``mail_id`` lookup returning the full mail. The mailbox
    data comes from the injected provider (implemented in the agents
    layer); the tool never touches another member's mailbox and has no
    write path.
    """

    def __init__(self, provider: MailboxMailsProvider, agent_id: str) -> None:
        """Initialize the tool.

        Args:
            provider: Async callable returning an agent's own mails as
                JSON-safe dicts (implemented in the agents layer; the
                mailbox stays out of the tools layer)
            agent_id: Owning agent id — the mailbox being searched
        """
        super().__init__(
            name="searchMails",
            description=(
                "检索你自己的邮箱（inbox + sent，只读），回答「谁让我做的」"
                "「我当时回了什么」这类问责问题。\n"
                "- query：关键词检索，空格分隔多词为 AND 关系，匹配主题、正文、"
                "收发地址；返回按时间从新到旧的命中列表（正文为节选）。\n"
                "- mail_id：按 id 精确取一封邮件的完整正文（id 可来自 query 命中、"
                "邮件索引或 trace）。\n"
                "query 与 mail_id 至少传一个；只查你自己的邮箱，查不到他人邮件。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "关键词，空格分隔多词为 AND 关系，匹配主题、正文、"
                            "发件人与收件人地址（大小写不敏感）"
                        ),
                    },
                    "mail_id": {
                        "type": "string",
                        "description": "邮件 id，精确取该邮件的完整正文",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "关键词检索的返回条数上限（默认 10，最大 20）",
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "required": [],
            },
        )
        self._provider = provider
        self._agent_id = agent_id

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Search the owning agent's own mailbox (read-only)."""
        query = kwargs.get("query")
        query = str(query).strip() if isinstance(query, str) else ""
        mail_id = kwargs.get("mail_id")
        mail_id = str(mail_id).strip() if isinstance(mail_id, str) else ""
        if not query and not mail_id:
            return ToolResult(
                success=False,
                data=None,
                error="query 与 mail_id 至少传一个",
            )

        mails = await self._provider(self._agent_id)

        if mail_id:
            for mail in mails:
                if mail.get("id") == mail_id:
                    return ToolResult(
                        success=True,
                        data={"mail": mail},
                        message=f"找到邮件 {mail_id}（{mail.get('box')}）",
                    )
            return ToolResult(
                success=False,
                data=None,
                error=f"你的邮箱里没有 id 为 '{mail_id}' 的邮件",
            )

        limit = kwargs.get("limit")
        limit = int(limit) if isinstance(limit, (int, float)) else SEARCH_MAILS_DEFAULT_LIMIT
        limit = max(1, min(limit, SEARCH_MAILS_MAX_LIMIT))

        terms = [term.lower() for term in query.split()]
        hits = [mail for mail in mails if _matches(mail, terms)]
        hits.sort(key=lambda mail: str(mail.get("timestamp") or ""), reverse=True)
        hits = hits[:limit]
        return ToolResult(
            success=True,
            data={
                "mails": [_summary(mail) for mail in hits],
                "count": len(hits),
                "query": query,
            },
            message=f"命中 {len(hits)} 封邮件（query={query!r}）",
        )
