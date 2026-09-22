"""Integration tests for mail semantics: cc fan-out and reply threading.

Threading is linked by id, not subject: ``in_reply_to`` points at the
upstream mail and ``thread_id`` is the thread root's id. The server does
NOT inherit thread_id from in_reply_to — a reply without an explicit
thread_id starts a new thread rooted at itself (Mail.create semantics).
"""

import uuid
from pathlib import Path

from tlon_client.sdk.client import TlonClient

from .conftest import _assert_delivered, _find_inbox_mail


async def test_cc_reaches_all_recipients(inproc_server: tuple[str, Path]) -> None:
    """A mail with to + cc lands in both mailboxes with correct fields."""
    base_url, tlon_home = inproc_server
    token = f"cc-{uuid.uuid4().hex}"

    async with TlonClient(base_url=base_url) as client:
        # First contact creates the mailboxes, making both known humans.
        await client.get_inbox(username="bob")
        await client.get_inbox(username="carol")
        accepted = await client.send_message(
            token, to=["bob"], cc=["carol"], subject="cc smoke"
        )

    assert accepted
    await _assert_delivered(tlon_home, "human-bob", token)
    await _assert_delivered(tlon_home, "human-carol", token)

    mail = _find_inbox_mail(tlon_home, "human-bob", token)
    assert mail["to"] == ["bob@tlon.internal"]
    assert mail["cc"] == ["carol@tlon.internal"]


async def test_reply_threads(inproc_server: tuple[str, Path]) -> None:
    """Replies record in_reply_to; thread_id is inherited only when passed."""
    base_url, tlon_home = inproc_server
    token_a = f"thread-a-{uuid.uuid4().hex}"
    token_b = f"thread-b-{uuid.uuid4().hex}"
    token_c = f"thread-c-{uuid.uuid4().hex}"

    async with TlonClient(base_url=base_url) as client:
        await client.get_inbox(username="bob")
        # A: guest -> bob (sender "guest" is the default identity; first
        # contact creates the human-guest mailbox).
        assert await client.send_message(token_a, to=["bob"], subject="A")
        await _assert_delivered(tlon_home, "human-bob", token_a)
        mail_a = _find_inbox_mail(tlon_home, "human-bob", token_a)
        assert mail_a["thread_id"] == mail_a["id"]  # a new thread roots at itself

        # B: reply to A with the upstream thread_id -> same thread.
        assert await client.send_message(
            token_b, to=["guest"], reply_to=mail_a["id"], thread_id=mail_a["thread_id"]
        )
        # C: reply to A without thread_id -> a new thread, no inheritance.
        assert await client.send_message(token_c, to=["guest"], reply_to=mail_a["id"])

    await _assert_delivered(tlon_home, "human-guest", token_b)
    await _assert_delivered(tlon_home, "human-guest", token_c)

    mail_b = _find_inbox_mail(tlon_home, "human-guest", token_b)
    assert mail_b["in_reply_to"] == mail_a["id"]
    assert mail_b["thread_id"] == mail_a["thread_id"]

    mail_c = _find_inbox_mail(tlon_home, "human-guest", token_c)
    assert mail_c["in_reply_to"] == mail_a["id"]
    assert mail_c["thread_id"] == mail_c["id"]
    assert mail_c["thread_id"] != mail_a["thread_id"]
