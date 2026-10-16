"""End-to-end mail smoke test against a real ``tlon dream`` subprocess.

Automates the manual smoke flow from AGENTS.md: boot the server with an
isolated TLON_HOME, send mail through the SDK, assert it lands in the
recipient's SQLite inbox. The delivery path (POST /messages -> SQLite
transaction) touches neither LLM nor Docker, so it runs in plain CI.

Division of labor: this file deliberately keeps the ``tlon_server``
subprocess fixture as the standing pin for the launcher/CLI entry point
and the process boundary; every other integration test drives ServerApp
in-process via the ``inproc_server`` fixture (see conftest.py).
"""

import uuid
from pathlib import Path

import aiohttp

from tlon_client.sdk.client import TlonClient

from .conftest import _assert_delivered


async def test_mail_to_agent_lands_in_inbox(tlon_server: tuple[str, Path]) -> None:
    """POST /messages commits the agent inbox before returning.

    The member is created through the admin API first (ADR 0016: first
    boot is an empty society — no standard team is seeded anymore).
    """
    base_url, tlon_home = tlon_server
    token = f"smoke-{uuid.uuid4().hex}"

    async with (
        aiohttp.ClientSession() as http,
        http.post(
            f"{base_url}/admin/config/agents",
            json={
                "prototype": "developer",
                "name": "林知遥",
                "model": "openai/gpt-test",
                "agent_id": "lin-zhiyao",
            },
        ) as resp,
    ):
        assert resp.status == 200

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
    await _assert_delivered(tlon_home, "human:bob", token)
