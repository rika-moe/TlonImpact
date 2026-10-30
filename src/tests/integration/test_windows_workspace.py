"""Portable HTTP mail smoke, included in the native Windows workspace gate."""

import uuid
from pathlib import Path

import aiohttp

from .conftest import WEBUI_HEADERS, _assert_delivered, _register_human, _send_mail


async def test_real_dream_http_mail_reaches_durable_inbox(
    tlon_server: tuple[str, Path],
) -> None:
    base_url, home = tlon_server
    _register_human(home, "windows-recipient")
    content = f"Windows HTTP 邮件 🐙 {uuid.uuid4().hex}"
    async with aiohttp.ClientSession(
        headers=WEBUI_HEADERS, timeout=aiohttp.ClientTimeout(total=10.0)
    ) as http:
        receipt = await _send_mail(
            http,
            base_url,
            content,
            to=["human:windows-recipient"],
            subject="Windows 原生冒烟",
        )
        assert receipt
    await _assert_delivered(home, "human:windows-recipient", content)
