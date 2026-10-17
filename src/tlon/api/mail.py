"""Mail facade: SQLite mailbox reads and transactional post-office receipts."""

import asyncio
from typing import Any

from tlon.agents.agent_registry import AgentRegistry
from tlon.base.paths import TlonPaths
from tlon.organization.mail.post_office import PostRejected
from tlon.organization.mail.store import IdempotencyConflict, MailPage, MailStore
from tlon.organization.messaging.human_adapter import (
    DEFAULT_USERNAME,
    HUMAN_ID_PREFIX,
    AdapterManager,
)

__all__ = ["DEFAULT_USERNAME", "MailFacade", "MailSubmissionError"]


class MailSubmissionError(ValueError):
    """A rejected submission exposed to serving layers."""

    def __init__(self, message: str, reason: str, invalid_recipients: list[str]):
        super().__init__(message)
        self.reason = reason
        self.invalid_recipients = invalid_recipients


class MailFacade:
    """邮件域北向 API。构造器注入 AdapterManager + AgentRegistry +
    TlonPaths，薄透传。

    Example:
        >>> mail = MailFacade(adapter_manager=am, agent_registry=reg, tlon_paths=paths)
        >>> mails = await mail.inbox("alice", limit=50)
        >>> receipt = await mail.send("alice", "你好", to=["谢云舒"])
    """

    def __init__(
        self,
        *,
        adapter_manager: AdapterManager,
        agent_registry: AgentRegistry,
        tlon_paths: TlonPaths,
    ):
        """Initialize the facade.

        Args:
            adapter_manager: Human adapter lifecycle (speak side)
            agent_registry: Agent existence lookup (member observation)
            tlon_paths: Path resolver (post-office database)
        """
        self._adapter_manager = adapter_manager
        self._registry = agent_registry
        self._paths = tlon_paths

    async def inbox(self, username: str, limit: int = 50) -> list[dict[str, Any]]:
        """The human member's own mail (inbox + sent merged, chronological).

        Reading does not mark mail read. Sent-only mail is shown as read;
        inbox read state belongs to its recipient.

        Args:
            username: Human username (the inbox of "human:<username>")
            limit: Maximum number of mails

        Returns:
            Mail dicts (Mail.to_dict) with an added "read" flag
        """
        return (await self.inbox_page(username, limit))["mails"]

    async def inbox_page(
        self,
        username: str,
        limit: int = 50,
        before: str | None = None,
    ) -> MailPage:
        """Page through human inbox and sent mail without marking read."""
        member_id = f"{HUMAN_ID_PREFIX}{username}"
        store = MailStore(self._paths.mail_database_file())
        await asyncio.to_thread(store.register_member, member_id)
        return await asyncio.to_thread(store.page, member_id, limit, before, include_read=True)

    async def member_inbox(self, member_id: str, limit: int = 50) -> list[dict[str, Any]] | None:
        """Observe recent mail without exposing recipient read state."""
        page = await self.member_inbox_page(member_id, limit)
        return None if page is None else page["mails"]

    async def member_inbox_page(
        self,
        member_id: str,
        limit: int = 50,
        before: str | None = None,
    ) -> MailPage | None:
        """Observe chronological pages without creating unknown mailboxes."""
        store = MailStore(self._paths.mail_database_file())
        exists = await asyncio.to_thread(store.member_exists, member_id)
        if not exists and (
            member_id.startswith(HUMAN_ID_PREFIX) or self._registry.get_config(member_id) is None
        ):
            return None
        return await asyncio.to_thread(store.page, member_id, limit, before)

    async def set_read(
        self,
        username: str,
        read: bool,
        *,
        mail_id: str | None = None,
        thread_id: str | None = None,
    ) -> list[str]:
        """Explicitly mark a human's incoming mail; observation never calls this."""
        store = MailStore(self._paths.mail_database_file())
        return await asyncio.to_thread(
            store.set_read,
            f"{HUMAN_ID_PREFIX}{username}",
            read,
            mail_id=mail_id,
            thread_id=thread_id,
        )

    async def send(
        self,
        username: str,
        content: str,
        *,
        to: list[str] | None = None,
        cc: list[str] | None = None,
        subject: str | None = None,
        in_reply_to: str | None = None,
        thread_id: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a mail as a human member (no client session required).

        If the human has no live adapter, a transient one is created —
        first contact also creates the persistent mailbox, same semantics
        as the first session attach.

        Args:
            username: Human username (sender identity)
            content: Mail content
            to: Recipients (names, ids or full addresses)
            cc: Carbon-copy recipients
            subject: Optional subject
            in_reply_to: Id of the mail being replied to
            thread_id: Thread root id inherited from the upstream mail

        Returns:
            Receipt with local inbox confirmations and gateway attempt results.

        Raises:
            MailSubmissionError: Any invalid/retired recipient or request-key conflict.
        """
        try:
            post = await self._adapter_manager.publish_as(
                username,
                content,
                to=to,
                cc=cc,
                subject=subject,
                in_reply_to=in_reply_to,
                thread_id=thread_id,
                request_id=request_id,
            )
        except IdempotencyConflict as exc:
            raise MailSubmissionError(str(exc), "idempotency_conflict", []) from exc
        except PostRejected as exc:
            raise MailSubmissionError(str(exc), exc.reason, exc.invalid_recipients) from exc
        return post.to_dict()
