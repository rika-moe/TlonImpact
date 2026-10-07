"""Unit tests for the Telegram bot client (docs/design/telegram.md).

ReplyMap / whitelist / /send parsing are pure logic and run in every
environment. Bot packaging (format_*) and the send/receive pipeline need
python-telegram-bot (the ``telegram`` extra) and are skipped without it
(``pytest.importorskip``); the Tlon side runs against an in-process
aiohttp v2 server (same pattern as test_sdk.py) while the Telegram side
is replaced by a fake deliver callable — no real Telegram API.
"""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from tlon_client.sdk.adapter import TelegramAdapter
from tlon_client.sdk.client import TlonClient
from tlon_client.telegram.reply_map import (
    ReplyMap,
    ReplyTarget,
    is_allowed,
    parse_allowed_users,
    parse_send_command,
)


async def wait_for(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll until condition() is true or fail after timeout."""
    for _ in range(int(timeout / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met within timeout")


def make_mail(**overrides: Any) -> dict[str, Any]:
    """A mail dict as it arrives in a mail.delivered payload / GET /inbox."""
    mail: dict[str, Any] = {
        "id": "mail-1",
        "from_address": "gu-yanxi@tlon.internal",
        "to": ["alice@tlon.internal"],
        "cc": [],
        "subject": "进度汇报",
        "content": "第一阶段已完成",
        "in_reply_to": None,
        "thread_id": "thread-1",
        "forward_of": None,
        "timestamp": "2026-08-24T00:00:00+00:00",
        "read": False,
    }
    mail.update(overrides)
    return mail


class FakeDeliver:
    """Deliver seam fake: records (chat_id, text), hands out message ids."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.message_ids: list[int] = []
        self._next_id = 100

    async def __call__(self, chat_id: int, text: str) -> int:
        self.sent.append((chat_id, text))
        self._next_id += 1
        self.message_ids.append(self._next_id)
        return self._next_id


@pytest.fixture
def bot_mod():
    """The bot module; tests needing it skip without the telegram extra."""
    pytest.importorskip("telegram")
    from tlon_client.telegram import bot

    return bot


# ==================== Pure logic: ReplyMap ====================


class TestReplyMap:
    """telegram_message_id -> mail mapping (bounded, LRU eviction)."""

    def test_record_and_lookup(self):
        reply_map = ReplyMap()
        reply_map.record(101, make_mail())

        target = reply_map.lookup(101)
        assert target == ReplyTarget(
            mail_id="mail-1",
            from_address="gu-yanxi@tlon.internal",
            thread_id="thread-1",
        )

    def test_lookup_missing(self):
        assert ReplyMap().lookup(999) is None

    def test_thread_id_defaults_to_mail_id(self):
        reply_map = ReplyMap()
        mail = make_mail()
        del mail["thread_id"]
        reply_map.record(1, mail)

        assert reply_map.lookup(1) == ReplyTarget(
            mail_id="mail-1",
            from_address="gu-yanxi@tlon.internal",
            thread_id="mail-1",
        )

    def test_eviction_bounded(self):
        reply_map = ReplyMap(capacity=3)
        for message_id in range(1, 5):
            reply_map.record(message_id, make_mail(id=f"mail-{message_id}"))

        assert len(reply_map) == 3
        assert reply_map.lookup(1) is None  # oldest evicted
        assert reply_map.lookup(2).mail_id == "mail-2"
        assert reply_map.lookup(4).mail_id == "mail-4"

    def test_re_record_refreshes_position(self):
        reply_map = ReplyMap(capacity=2)
        reply_map.record(1, make_mail(id="mail-1"))
        reply_map.record(2, make_mail(id="mail-2"))
        reply_map.record(1, make_mail(id="mail-1b"))  # refresh: 2 is now oldest
        reply_map.record(3, make_mail(id="mail-3"))

        assert reply_map.lookup(2) is None
        assert reply_map.lookup(1).mail_id == "mail-1b"
        assert reply_map.lookup(3).mail_id == "mail-3"

    def test_last_from_address(self):
        reply_map = ReplyMap()
        assert reply_map.last_from_address is None

        reply_map.record(1, make_mail(from_address="a@tlon.internal"))
        assert reply_map.last_from_address == "a@tlon.internal"

        reply_map.record(2, make_mail(from_address="b@tlon.internal"))
        assert reply_map.last_from_address == "b@tlon.internal"

    def test_capacity_must_be_positive(self):
        with pytest.raises(ValueError):
            ReplyMap(capacity=0)


# ==================== Pure logic: whitelist ====================


class TestParseAllowedUsers:
    """TELEGRAM_ALLOWED_USERS parsing (comma-separated user ids)."""

    def test_normal(self):
        assert parse_allowed_users("123, 456 ,789") == [123, 456, 789]

    def test_empty(self):
        assert parse_allowed_users("") == []
        assert parse_allowed_users(" , ,  ") == []

    def test_single(self):
        assert parse_allowed_users("42") == [42]

    def test_invalid_entry_raises(self):
        with pytest.raises(ValueError, match="abc"):
            parse_allowed_users("123,abc")
        with pytest.raises(ValueError, match="12a"):
            parse_allowed_users("12a")


class TestIsAllowed:
    """Whitelist gate: messages from outside are ignored (design D1)."""

    def test_allowed(self):
        assert is_allowed(123, [123, 456]) is True

    def test_not_allowed(self):
        assert is_allowed(999, [123, 456]) is False

    def test_no_user(self):
        assert is_allowed(None, [123]) is False

    def test_empty_whitelist_allows_nobody(self):
        assert is_allowed(123, []) is False


# ==================== Pure logic: /send parsing ====================


class TestParseSendCommand:
    """'/send <名字> <内容>' parsing, malformed input included."""

    def test_normal(self):
        assert parse_send_command("顾言蹊 你好") == ("顾言蹊", "你好")

    def test_content_keeps_inner_spaces(self):
        assert parse_send_command("dev-001 多行 内容 带空格") == ("dev-001", "多行 内容 带空格")

    def test_surrounding_whitespace(self):
        assert parse_send_command("  顾言蹊   你好  ") == ("顾言蹊", "你好")

    def test_missing_content(self):
        assert parse_send_command("顾言蹊") is None
        assert parse_send_command("顾言蹊  ") is None

    def test_empty(self):
        assert parse_send_command("") is None
        assert parse_send_command("   ") is None


# ==================== Bot packaging (needs the telegram extra) ====================


class TestFormat:
    """Message rendering: sender display name, subject, truncation."""

    def test_sender_name(self, bot_mod):
        assert bot_mod.sender_name("gu-yanxi@tlon.internal") == "gu-yanxi"
        assert bot_mod.sender_name("bare") == "bare"

    def test_format_mail_with_subject(self, bot_mod):
        text = bot_mod.format_mail(make_mail())
        assert "gu-yanxi" in text
        assert "主题：进度汇报" in text
        assert "第一阶段已完成" in text

    def test_format_mail_without_subject(self, bot_mod):
        text = bot_mod.format_mail(make_mail(subject=None))
        assert "主题" not in text
        assert "第一阶段已完成" in text

    def test_format_mail_truncates_long_content(self, bot_mod):
        text = bot_mod.format_mail(make_mail(content="长" * 5000))
        assert len(text) == bot_mod.MAX_MESSAGE_LEN
        assert text.endswith("…")

    def test_format_unread_list(self, bot_mod):
        unread = [
            make_mail(),
            make_mail(id="mail-2", from_address="adam@tlon.internal", subject=None),
        ]
        text = bot_mod.format_unread_list(unread, "📬 2 封未读：")
        lines = text.splitlines()
        assert lines[0] == "📬 2 封未读："
        assert lines[1] == "- gu-yanxi：进度汇报"
        assert lines[2] == "- adam：(无主题)"


class TestPushMail:
    """mail.delivered -> Telegram push (deliver seam faked, no PTB API)."""

    async def test_push_to_all_whitelisted_users(self, bot_mod):
        fake = FakeDeliver()
        bot = bot_mod.TelegramBot(
            token="t",
            allowed_users=[111, 222],
            username="alice",
            base_url="http://unused",
            deliver=fake,
        )

        await bot.push_mail({"recipient": "human:alice", "mail": make_mail()})

        assert [chat_id for chat_id, _ in fake.sent] == [111, 222]
        assert "gu-yanxi" in fake.sent[0][1]
        # Both pushed messages are mapped back to the mail for replies
        for message_id in fake.message_ids:
            assert bot._reply_map.lookup(message_id).mail_id == "mail-1"
        assert bot._reply_map.last_from_address == "gu-yanxi@tlon.internal"

    async def test_push_ignores_malformed_payload(self, bot_mod):
        fake = FakeDeliver()
        bot = bot_mod.TelegramBot(
            token="t",
            allowed_users=[111],
            username="alice",
            base_url="http://unused",
            deliver=fake,
        )

        await bot.push_mail({})
        await bot.push_mail({"mail": "not-a-dict"})

        assert fake.sent == []


# ==================== In-process v2 server (same pattern as test_sdk.py) ====================


def make_test_app() -> web.Application:
    """In-process v2 server: sessions + SSE + messages + inbox."""
    app = web.Application()
    app["sse_queues"] = {}
    app["requests"] = []  # captured (method, path, body)

    async def create_session(request: web.Request) -> web.Response:
        body = await request.json()
        app["requests"].append(("POST", "/sessions", body))
        session = {
            "session_id": "telegram-1",
            "username": body.get("username", "guest"),
            "client_type": body.get("client_type", "unknown"),
            "mode": body.get("mode", "chat"),
            "filters": body.get("filters") or {},
            "connected_at": "2026-08-24T00:00:00Z",
        }
        return web.json_response({**session, "participants": [], "agents": []}, status=201)

    async def delete_session(request: web.Request) -> web.Response:
        session_id = request.match_info["session_id"]
        queue = app["sse_queues"].get(session_id)
        if queue is not None:
            queue.put_nowait(None)
        return web.json_response({"session_id": session_id, "status": "disconnected"})

    async def events(request: web.Request) -> web.StreamResponse:
        session_id = request.query.get("session_id")
        queue: asyncio.Queue = asyncio.Queue()
        app["sse_queues"][session_id] = queue
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        try:
            while True:
                frame = await queue.get()
                if frame is None:
                    break
                await resp.write(frame.encode("utf-8"))
        finally:
            app["sse_queues"].pop(session_id, None)
        return resp

    async def send_message(request: web.Request) -> web.Response:
        body = await request.json()
        app["requests"].append(("POST", "/messages", body))
        return web.json_response({"mail_id": "out-1"}, status=202)

    async def inbox(request: web.Request) -> web.Response:
        app["requests"].append(("GET", "/inbox", dict(request.query)))
        return web.json_response(
            {
                "mails": [
                    make_mail(),
                    make_mail(id="mail-0", subject="旧信", read=True),
                ],
                "count": 2,
            }
        )

    app.router.add_post("/sessions", create_session)
    app.router.add_delete("/sessions/{session_id}", delete_session)
    app.router.add_get("/events", events)
    app.router.add_post("/messages", send_message)
    app.router.add_get("/inbox", inbox)
    return app


@pytest.fixture
async def server():
    """Start the in-process test server."""
    test_server = TestServer(make_test_app())
    await test_server.start_server()
    yield test_server
    await test_server.close()


@pytest.fixture
def base_url(server):
    return str(server.make_url(""))


@pytest.fixture
async def client(base_url):
    """A connected TlonClient (TelegramAdapter) against the test server."""
    client = TlonClient(base_url=base_url, adapter=TelegramAdapter(), auto_reconnect=False)
    await client.connect()
    yield client
    await client.disconnect()


@pytest.fixture
def bot(bot_mod, base_url):
    """The bot under test: real routing logic, fake Telegram delivery."""
    fake = FakeDeliver()
    bot = bot_mod.TelegramBot(
        token="t",
        allowed_users=[111, 222],
        username="alice",
        base_url=base_url,
        deliver=fake,
    )
    bot.fake = fake
    return bot


# ==================== Send/receive pipeline (needs the telegram extra) ====================


class TestPipeline:
    """End-to-end over the SDK transport; Telegram side faked."""

    async def test_session_declares_telegram_chat_mode(self, client, server):
        """authenticate() speaks protocol v2 as a telegram chat client."""
        assert await client.authenticate(username="alice") is True

        _, _, body = server.app["requests"][0]
        assert body["client_type"] == "telegram"
        assert body["mode"] == "chat"
        assert body["filters"] == {"event_types": ["mail.*"]}
        assert body["username"] == "alice"

    async def test_receive_push_and_threaded_reply(self, bot, client, server):
        """mail.delivered -> push; Telegram reply -> in_reply_to/thread_id mail."""
        await client.authenticate(username="alice")
        bot._client = client
        client.on_event(bot._on_tlon_event)
        await wait_for(lambda: "telegram-1" in server.app["sse_queues"])

        mail = make_mail()
        frame = {
            "type": "event",
            "event_type": "mail.delivered",
            "payload": {"recipient": "human:alice", "mail": mail},
        }
        server.app["sse_queues"]["telegram-1"].put_nowait(f"data: {json.dumps(frame)}\n\n")

        await wait_for(lambda: len(bot.fake.sent) == 2)
        assert bot.fake.sent[0][0] == 111
        assert mail["content"] in bot.fake.sent[0][1]

        ack = await bot.process_reply("收到，多谢", reply_to_message_id=bot.fake.message_ids[0])

        assert ack == "已回复 ✅"
        _, _, body = [r for r in server.app["requests"] if r[1] == "/messages"][0]
        assert body["to"] == [mail["from_address"]]
        assert body["in_reply_to"] == mail["id"]
        assert body["thread_id"] == mail["thread_id"]
        assert body["username"] == "alice"
        assert body["content"] == "收到，多谢"

    async def test_reply_with_lost_mapping_degrades_to_plain_mail(self, bot, client, server):
        """Restart/eviction: reply goes out as a new mail to the last sender."""
        await client.authenticate(username="alice")
        bot._client = client
        # One recorded mail establishes the last correspondent; the replied-to
        # message id (999) is unknown — as after a restart
        bot._reply_map.record(1, make_mail())

        ack = await bot.process_reply("还在吗", reply_to_message_id=999)

        assert "映射已丢失" in ack
        _, _, body = [r for r in server.app["requests"] if r[1] == "/messages"][0]
        assert body["to"] == ["gu-yanxi@tlon.internal"]
        assert "in_reply_to" not in body
        assert "thread_id" not in body

    async def test_plain_text_without_any_mapping_gets_guidance(self, bot):
        """No reply context and no correspondent: usage hint, nothing sent."""
        ack = await bot.process_reply("你好", reply_to_message_id=None)
        assert "回复" in ack and "/send" in ack

    async def test_send_mail_posts_message(self, bot, client, server):
        """'/send 名字 内容' packaging: POST /messages with the parsed recipient."""
        await client.authenticate(username="alice")
        bot._client = client

        assert await bot.send_mail("你好", to=["顾言蹊"]) is True

        _, _, body = [r for r in server.app["requests"] if r[1] == "/messages"][0]
        assert body["to"] == ["顾言蹊"]
        assert body["content"] == "你好"
        assert "in_reply_to" not in body

    async def test_unread_summary_backfill(self, bot, client, server):
        """Startup backfill: unread summary pushed to every whitelisted user."""
        await client.authenticate(username="alice")
        bot._client = client

        await bot.push_unread_summary()

        # One unread (the read one is filtered out), pushed to both users
        assert [chat_id for chat_id, _ in bot.fake.sent] == [111, 222]
        text = bot.fake.sent[0][1]
        assert "1 封未读" in text
        assert "gu-yanxi：进度汇报" in text

        _, _, query = [r for r in server.app["requests"] if r[1] == "/inbox"][0]
        assert query["username"] == "alice"
