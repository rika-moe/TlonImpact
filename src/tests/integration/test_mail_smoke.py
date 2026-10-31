"""End-to-end mail smoke test against a real ``tlon dream`` subprocess.

Automates the manual smoke flow from AGENTS.md: boot the server with an
isolated TLON_HOME, send mail through the WebUI HTTP protocol, assert it lands in the
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

from tests.prototype_helpers import builtin_id

from .conftest import WEBUI_HEADERS, _assert_delivered, _register_human, _send_mail


async def test_mail_to_agent_lands_in_inbox(tlon_server: tuple[str, Path]) -> None:
    """POST /messages commits the agent inbox before returning.

    Create an explicit test-model member alongside the initial society,
    so this delivery test does not invoke the seeded DeepSeek models.
    """
    base_url, tlon_home = tlon_server
    token = f"smoke-{uuid.uuid4().hex}"

    async with (
        aiohttp.ClientSession(
            headers=WEBUI_HEADERS, timeout=aiohttp.ClientTimeout(total=30.0)
        ) as http,
        http.post(
            f"{base_url}/admin/config/agents",
            json={
                "prototype": builtin_id("标准智能体"),
                "name": "林知遥",
                "model": "openai/gpt-4o",
            },
        ) as resp,
    ):
        assert resp.status == 200
        agent_id = (await resp.json())["agent_id"]

    async with aiohttp.ClientSession(
        headers=WEBUI_HEADERS, timeout=aiohttp.ClientTimeout(total=30.0)
    ) as http:
        accepted = await _send_mail(http, base_url, token, to=[agent_id], subject="smoke")

    assert accepted
    await _assert_delivered(tlon_home, agent_id, token)


async def test_mail_to_known_human_lands_in_inbox(tlon_server: tuple[str, Path]) -> None:
    """Mail to a known human waits in their inbox (human -> human path)."""
    base_url, tlon_home = tlon_server
    token = f"smoke-{uuid.uuid4().hex}"

    async with aiohttp.ClientSession(
        headers=WEBUI_HEADERS, timeout=aiohttp.ClientTimeout(total=30.0)
    ) as http:
        # Reading bob's inbox creates his mailbox, making him a known human.
        _register_human(tlon_home, "bob")
        accepted = await _send_mail(http, base_url, token, to=["bob"], subject="smoke")

    assert accepted
    await _assert_delivered(tlon_home, "human:bob", token)
