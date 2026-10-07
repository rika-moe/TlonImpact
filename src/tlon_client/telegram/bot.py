"""Telegram bot for Tlon (design: docs/design/telegram.md).

One process = python-telegram-bot (polling) + the official SDK TlonClient
(TelegramAdapter, chat mode) sharing a single asyncio event loop:

- ``mail.delivered`` -> push one Telegram message per whitelisted user,
  recording telegram_message_id -> mail for reply threading (D2);
- a Telegram reply to a pushed message -> mail with in_reply_to/thread_id
  back to the original sender; a lost mapping (restart / eviction)
  degrades to a plain mail to the last correspondent, never an error;
- ``/send <名字> <内容>`` sends a new mail, ``/inbox`` lists unread mail,
  and an unread summary is pushed at startup (offline backfill).

Requires the ``telegram`` optional extra (``uv sync --extra telegram``).
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from tlon_client.sdk.adapter import TelegramAdapter
from tlon_client.sdk.client import TlonClient

from .reply_map import ReplyMap, is_allowed, parse_send_command

logger = structlog.get_logger(__name__)

# Telegram messages are capped at 4096 chars; keep headroom for the header.
MAX_MESSAGE_LEN = 4000

# How many recent mails /inbox and the startup backfill look at.
INBOX_LIMIT = 20

# Outbound seam: (chat_id, text) -> telegram message_id of the sent message.
DeliverFunc = Callable[[int, str], Awaitable[int]]


def sender_name(from_address: str) -> str:
    """Display name for a mail address: its local part."""
    return from_address.partition("@")[0]


def format_mail(mail: dict[str, Any]) -> str:
    """Render an incoming mail as one Telegram message (long mails truncated).

    Args:
        mail: Mail dict (from_address, subject, content, ...)

    Returns:
        The message text: sender display name, optional subject, content
    """
    lines = [f"✉️ {sender_name(str(mail['from_address']))}"]
    subject = mail.get("subject")
    if subject:
        lines.append(f"主题：{subject}")
    lines.append("")
    lines.append(str(mail.get("content") or ""))
    text = "\n".join(lines)
    if len(text) > MAX_MESSAGE_LEN:
        text = text[: MAX_MESSAGE_LEN - 1] + "…"
    return text


def format_unread_list(unread: list[dict[str, Any]], header: str) -> str:
    """Render an unread-mail list (one "sender：subject" line per mail).

    Args:
        unread: Unread mail dicts
        header: First line of the message

    Returns:
        The message text (truncated to the Telegram limit)
    """
    lines = [header]
    for mail in unread:
        subject = mail.get("subject") or "(无主题)"
        lines.append(f"- {sender_name(str(mail['from_address']))}：{subject}")
    text = "\n".join(lines)
    if len(text) > MAX_MESSAGE_LEN:
        text = text[: MAX_MESSAGE_LEN - 1] + "…"
    return text


class TelegramBot:
    """The Telegram bot: PTB polling + TlonClient in one event loop.

    Args:
        token: Telegram bot token (TELEGRAM_BOT_TOKEN)
        allowed_users: Telegram user-id whitelist (TELEGRAM_ALLOWED_USERS)
        username: Tlon mail identity the bot speaks as ("human:<username>")
        base_url: Tlon server REST base URL
        deliver: Optional seam for tests — (chat_id, text) -> message_id;
            defaults to the PTB application's bot.send_message
    """

    def __init__(
        self,
        *,
        token: str,
        allowed_users: list[int],
        username: str,
        base_url: str,
        deliver: DeliverFunc | None = None,
    ) -> None:
        self._token = token
        self._allowed_users = allowed_users
        self._username = username
        self._base_url = base_url
        self._deliver = deliver
        self._reply_map = ReplyMap()
        self._client: TlonClient | None = None

    # ==================== Lifecycle ====================

    async def run(self) -> None:
        """Run polling + the SDK client until cancelled (Ctrl-C).

        PTB's Application is driven manually (initialize/start/start_polling)
        instead of run_polling() so it shares the event loop with the SDK
        client instead of owning one.
        """
        app = Application.builder().token(self._token).build()
        app.add_handler(CommandHandler("send", self._on_send_command))
        app.add_handler(CommandHandler("inbox", self._on_inbox_command))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        if self._deliver is None:
            self._deliver = self._make_deliver(app)

        async with TlonClient(self._base_url, adapter=TelegramAdapter()) as client:
            self._client = client
            await client.authenticate(username=self._username)
            client.on_event(self._on_tlon_event)

            await app.initialize()
            await app.start()
            updater = app.updater
            assert updater is not None  # the polling updater is built by default
            await updater.start_polling()
            logger.info(
                "telegram_bot_started",
                username=self._username,
                allowed_users=self._allowed_users,
            )
            try:
                await self.push_unread_summary()
                await asyncio.Event().wait()  # until cancelled (Ctrl-C)
            finally:
                await updater.stop()
                await app.stop()
                await app.shutdown()
                self._client = None

    @staticmethod
    def _make_deliver(app: Any) -> DeliverFunc:
        """Build the default deliver seam on the PTB application's bot.

        Args:
            app: The built PTB Application (Any: its full generic
                specialization is irrelevant here)
        """

        async def deliver(chat_id: int, text: str) -> int:
            message = await app.bot.send_message(chat_id=chat_id, text=text)
            return int(message.message_id)

        return deliver

    # ==================== Tlon -> Telegram ====================

    def _on_tlon_event(self, event: dict[str, Any]) -> None:
        """SDK event callback (sync): schedule the mail push as a task.

        The session declares mail-only delivery (TelegramAdapter), so in
        practice only mail.* events arrive; non-mail events are ignored
        defensively.
        """
        if event.get("type") != "mail.delivered":
            return
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        asyncio.create_task(self.push_mail(payload))

    async def push_mail(self, payload: dict[str, Any]) -> None:
        """Push a delivered mail to every whitelisted Telegram user (D2).

        Args:
            payload: The mail.delivered payload ({"recipient", "mail"})
        """
        mail = payload.get("mail")
        if not isinstance(mail, dict) or "id" not in mail:
            return
        if self._deliver is None:
            raise RuntimeError("push_mail called before run()")
        text = format_mail(mail)
        for user_id in self._allowed_users:
            try:
                message_id = await self._deliver(user_id, text)
            except Exception as e:
                logger.error("telegram_push_failed", user_id=user_id, error=str(e))
                continue
            self._reply_map.record(message_id, mail)

    async def push_unread_summary(self) -> None:
        """Offline backfill: push an unread-mail summary at startup (D2)."""
        if self._client is None or self._deliver is None:
            raise RuntimeError("push_unread_summary called before run()")
        unread = await self._fetch_unread()
        if not unread:
            return
        text = format_unread_list(unread, f"📬 离线期间共 {len(unread)} 封未读（/inbox 查看）：")
        for user_id in self._allowed_users:
            try:
                await self._deliver(user_id, text)
            except Exception as e:
                logger.error("telegram_summary_failed", user_id=user_id, error=str(e))

    async def _fetch_unread(self) -> list[dict[str, Any]]:
        """Fetch unread mail (GET /inbox, filter read == false)."""
        if self._client is None:
            raise RuntimeError("client not connected")
        mails = await self._client.get_inbox(username=self._username, limit=INBOX_LIMIT)
        return [m for m in mails if m.get("read") is False]

    # ==================== Telegram -> Tlon ====================

    async def send_mail(
        self,
        content: str,
        to: list[str],
        reply_to: str | None = None,
        thread_id: str | None = None,
    ) -> bool:
        """Send a mail through the SDK client (POST /messages).

        Args:
            content: Mail content
            to: Recipients (names, ids or full addresses)
            reply_to: Optional in_reply_to mail id
            thread_id: Optional thread id inherited from the upstream mail

        Returns:
            True if accepted by the server
        """
        if self._client is None:
            raise RuntimeError("send_mail called before run()")
        return await self._client.send_message(
            content, to=to, reply_to=reply_to, thread_id=thread_id
        )

    async def process_reply(self, text: str, reply_to_message_id: int | None) -> str:
        """Route an inbound Telegram text and produce the ack message.

        A reply to a pushed message goes back to the original sender with
        in_reply_to/thread_id. A lost mapping degrades to a plain mail to
        the last correspondent (D2: restart/eviction must not error out).

        Args:
            text: The message text
            reply_to_message_id: ``reply_to_message.message_id`` when the
                Telegram message is a reply, else None

        Returns:
            The ack/hint text to answer with
        """
        target = (
            self._reply_map.lookup(reply_to_message_id) if reply_to_message_id is not None else None
        )
        if target is not None:
            ok = await self.send_mail(
                text,
                to=[target.from_address],
                reply_to=target.mail_id,
                thread_id=target.thread_id,
            )
            return "已回复 ✅" if ok else "发送失败：收件人无法解析"

        if reply_to_message_id is not None:
            last = self._reply_map.last_from_address
            if last is not None:
                ok = await self.send_mail(text, to=[last])
                if ok:
                    return "原信映射已丢失，已作为新邮件发给最近来信者 ✅"
                return "发送失败：收件人无法解析"

        return "请对某条来信点「回复」回信，或用 /send <名字> <内容> 发新邮件"

    # ==================== PTB handlers (thin wrappers) ====================

    def _user_id(self, update: Update) -> int | None:
        """The update's Telegram user id (None when it carries no user)."""
        user = update.effective_user
        return user.id if user else None

    async def _on_send_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/send <名字> <内容> — send a new mail (D2)."""
        message = update.effective_message
        if message is None or not is_allowed(self._user_id(update), self._allowed_users):
            return
        body = (message.text or "").partition(" ")[2]
        parsed = parse_send_command(body)
        if parsed is None:
            await message.reply_text("用法：/send <名字> <内容>")
            return
        name, content = parsed
        ok = await self.send_mail(content, to=[name])
        await message.reply_text("已发送 ✅" if ok else "发送失败：收件人无法解析")

    async def _on_inbox_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/inbox — list unread mail (D2)."""
        message = update.effective_message
        if message is None or not is_allowed(self._user_id(update), self._allowed_users):
            return
        unread = await self._fetch_unread()
        if not unread:
            await message.reply_text("📭 没有未读邮件")
            return
        await message.reply_text(format_unread_list(unread, f"📬 {len(unread)} 封未读："))

    async def _on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Plain text — a reply threads back onto the upstream mail (D2)."""
        message = update.effective_message
        if message is None or not message.text:
            return
        if not is_allowed(self._user_id(update), self._allowed_users):
            return
        reply = message.reply_to_message
        reply_to_id = reply.message_id if reply else None
        ack = await self.process_reply(message.text, reply_to_id)
        await message.reply_text(ack)
