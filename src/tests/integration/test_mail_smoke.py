"""End-to-end mail smoke test against a real ``tlon dream`` subprocess.

Automates the manual smoke flow from AGENTS.md: boot the server with an
isolated TLON_HOME, send mail through the SDK, assert it lands in the
recipient's inbox.jsonl. The delivery path (POST /messages -> event bus
-> MailDelivery) touches neither LLM nor Docker, so it runs in plain CI.

The server fixture and shared helpers live in conftest.py.
"""

import uuid
from pathlib import Path

from tlon.app.clients.sdk.client import TlonClient

from .conftest import _assert_delivered


async def test_mail_to_agent_lands_in_inbox(tlon_server: tuple[str, Path]) -> None:
    """POST /messages -> event bus -> MailDelivery -> agent inbox.jsonl."""
    base_url, tlon_home = tlon_server
    token = f"smoke-{uuid.uuid4().hex}"

    async with TlonClient(base_url=base_url) as client:
        accepted = await client.send_message(token, to=["lin-zhiyao"], subject="smoke")

    assert accepted
    await _assert_delivered(tlon_home, "lin-zhiyao", token)


async def test_mail_to_known_human_lands_in_inbox(tlon_server: tuple[str, Path]) -> None:
    """Mail to a known human waits in their inbox (human -> human path)."""
    base_url, tlon_home = tlon_server
    token = f"smoke-{uuid.uuid4().hex}"

    async with TlonClient(base_url=base_url) as client:
        # Reading bob's inbox creates his mailbox, making him a known human.
        await client.get_inbox(username="bob")
        accepted = await client.send_message(token, to=["bob"], subject="smoke")

    assert accepted
    await _assert_delivered(tlon_home, "human-bob", token)
