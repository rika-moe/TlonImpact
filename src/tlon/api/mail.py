"""Mail facade：人类成员的邮箱读与发信。

收编 REST 层对邮件内脏的两个直接触点：

- inbox 读：直接构造 ``Mailbox`` 读 jsonl（``recent``/``unread``）并
  合成 read 标记 —— 收进 :meth:`MailFacade.inbox`，返回可直接 JSON
  序列化的 dict
- 发信：``AdapterManager.publish_as``（无会话身份发信，首次接触建
  持久邮箱）—— 收进 :meth:`MailFacade.send`

``DEFAULT_USERNAME`` 是协议常量（请求缺省用户名），作为本模块 API
面的一部分对 server 可见。
"""

from typing import Any

from tlon.base.paths import TlonPaths
from tlon.organization.mail.mailbox import Mailbox
from tlon.organization.messaging.human_adapter import (
    DEFAULT_USERNAME,
    HUMAN_ID_PREFIX,
    AdapterManager,
)

__all__ = ["DEFAULT_USERNAME", "MailFacade"]


class MailFacade:
    """邮件域北向 API。构造器注入 AdapterManager + TlonPaths，薄透传。

    Example:
        >>> mail = MailFacade(adapter_manager=am, tlon_paths=paths)
        >>> mails = await mail.inbox("alice", limit=50)
        >>> mail_id = await mail.send("alice", "你好", to=["谢云舒"])
    """

    def __init__(self, *, adapter_manager: AdapterManager, tlon_paths: TlonPaths):
        """Initialize the facade.

        Args:
            adapter_manager: Human adapter lifecycle (speak side)
            tlon_paths: Path resolver (member mailbox directories)
        """
        self._adapter_manager = adapter_manager
        self._paths = tlon_paths

    async def inbox(self, username: str, limit: int = 50) -> list[dict[str, Any]]:
        """The human member's own mail (inbox + sent merged, chronological).

        Read semantics: an inbox mail is read when its id is in the
        cursor; sent mail is always read (the sender has seen it).

        Args:
            username: Human username (the inbox of "human:<username>")
            limit: Maximum number of mails

        Returns:
            Mail dicts (Mail.to_dict) with an added "read" flag
        """
        mail_dir = self._paths.member_mail_dir(f"{HUMAN_ID_PREFIX}{username}")
        # A human becomes "known" on first inbox read (docs/design/mail.md);
        # materialize their persistent mailbox explicitly.
        mail_dir.mkdir(parents=True, exist_ok=True)
        mailbox = Mailbox(mail_dir)
        mails = await mailbox.recent(limit)
        unread_ids = {m.id for m in await mailbox.unread()}
        result = []
        for mail in mails:
            item = mail.to_dict()
            item["read"] = mail.id not in unread_ids
            result.append(item)
        return result

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
    ) -> str:
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
            The created mail's id

        Raises:
            ValueError: If no recipient resolves to a mail address
        """
        mail = await self._adapter_manager.publish_as(
            username,
            content,
            to=to,
            cc=cc,
            subject=subject,
            in_reply_to=in_reply_to,
            thread_id=thread_id,
        )
        return mail.id
