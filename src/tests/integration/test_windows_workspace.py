"""Portable SDK mail smoke, included in the native Windows workspace gate."""

import uuid
from pathlib import Path

from tlon_client.sdk.client import TlonClient

from .conftest import _assert_delivered, _register_human


async def test_real_dream_sdk_mail_reaches_durable_inbox(
    tlon_server: tuple[str, Path],
) -> None:
    base_url, home = tlon_server
    _register_human(home, "windows-recipient")
    content = f"Windows SDK 邮件 🐙 {uuid.uuid4().hex}"
    async with TlonClient(base_url, auto_reconnect=False) as client:
        # SDK has no public header injection API; explicitly opt into the
        # existing loopback WebUI boundary used by this initialized instance.
        assert client._base._http is not None
        client._base._http.headers.update({"X-Tlon-WebUI": "1"})
        receipt = await client.send_message(
            content, to=["human:windows-recipient"], subject="Windows 原生冒烟"
        )
        assert receipt
    await _assert_delivered(home, "human:windows-recipient", content)
