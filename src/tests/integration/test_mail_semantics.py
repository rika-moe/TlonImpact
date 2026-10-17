"""Integration tests for mail semantics: cc fan-out and reply threading.

Threading is linked by id, not subject: ``in_reply_to`` points at the
upstream mail and ``thread_id`` is the thread root's id. The server does
NOT inherit thread_id from in_reply_to — a reply without an explicit
thread_id starts a new thread rooted at itself (Mail.create semantics).
"""

import uuid
from pathlib import Path

import aiohttp

from .conftest import (
    WEBUI_HEADERS,
    _assert_delivered,
    _find_inbox_mail,
    _register_human,
    _send_mail,
)


async def test_cc_reaches_all_recipients(inproc_server: tuple[str, Path]) -> None:
    """A mail with to + cc lands in both mailboxes with correct fields."""
    base_url, tlon_home = inproc_server
    token = f"cc-{uuid.uuid4().hex}"

    async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
        # First contact creates the mailboxes, making both known humans.
        _register_human(tlon_home, "bob")
        _register_human(tlon_home, "carol")
        accepted = await _send_mail(
            http, base_url, token, to=["bob"], cc=["carol"], subject="cc smoke"
        )

    assert accepted
    await _assert_delivered(tlon_home, "human:bob", token)
    await _assert_delivered(tlon_home, "human:carol", token)

    mail = _find_inbox_mail(tlon_home, "human:bob", token)
    assert mail["to"] == ["bob@tlon.internal"]
    assert mail["cc"] == ["carol@tlon.internal"]


async def test_reply_threads(inproc_server: tuple[str, Path]) -> None:
    """Replies record in_reply_to; thread_id is inherited only when passed."""
    base_url, tlon_home = inproc_server
    token_a = f"thread-a-{uuid.uuid4().hex}"
    token_b = f"thread-b-{uuid.uuid4().hex}"
    token_c = f"thread-c-{uuid.uuid4().hex}"

    async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
        _register_human(tlon_home, "bob")
        # A: the configured alice identity sends to a known correspondent.
        assert await _send_mail(http, base_url, token_a, to=["bob"], subject="A")
        await _assert_delivered(tlon_home, "human:bob", token_a)
        mail_a = _find_inbox_mail(tlon_home, "human:bob", token_a)
        assert mail_a["thread_id"] == mail_a["id"]  # a new thread roots at itself

        # B: reply to A with the upstream thread_id -> same thread.
        assert await _send_mail(
            http,
            base_url,
            token_b,
            to=["alice"],
            reply_to=mail_a["id"],
            thread_id=mail_a["thread_id"],
        )
        # C: reply to A without thread_id -> a new thread, no inheritance.
        assert await _send_mail(http, base_url, token_c, to=["alice"], reply_to=mail_a["id"])

    await _assert_delivered(tlon_home, "human:alice", token_b)
    await _assert_delivered(tlon_home, "human:alice", token_c)

    mail_b = _find_inbox_mail(tlon_home, "human:alice", token_b)
    assert mail_b["in_reply_to"] == mail_a["id"]
    assert mail_b["thread_id"] == mail_a["thread_id"]

    mail_c = _find_inbox_mail(tlon_home, "human:alice", token_c)
    assert mail_c["in_reply_to"] == mail_a["id"]
    assert mail_c["thread_id"] == mail_c["id"]
    assert mail_c["thread_id"] != mail_a["thread_id"]
