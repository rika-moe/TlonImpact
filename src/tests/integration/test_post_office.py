"""Real HTTP/SDK contracts: committed inboxes, restarts and whole-post refusal."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiohttp

from tlon.base.paths import TlonPaths
from tlon.base.settings import Settings
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.store import MailStore
from tlon_client.sdk.client import TlonClient
from tlon_server.serverapp import ServerApp

from .conftest import _free_port, _wait_ready_http


@asynccontextmanager
async def _server(home: Path) -> AsyncGenerator[tuple[str, ServerApp]]:
    """Restartable real server, always pinned to the test's temporary home."""
    port = _free_port()
    app = ServerApp(
        host="127.0.0.1",
        rest_port=port,
        tlon_paths=TlonPaths(home=home),
        settings=Settings.from_env(),
    )
    await app.initialize()
    task = asyncio.create_task(app.start())
    base_url = f"http://127.0.0.1:{port}"
    try:
        await _wait_ready_http(base_url, task)
        yield base_url, app
    finally:
        await asyncio.wait_for(app.shutdown(), timeout=15)
        await asyncio.wait_for(task, timeout=5)


async def test_success_is_durable_and_replay_survives_server_restart(tmp_path: Path) -> None:
    database = TlonPaths(home=tmp_path).mail_database_file()
    async with _server(tmp_path) as (url, app):
        assert app._runtime is not None
        async with TlonClient(url) as client:
            await client.get_inbox(username="bob")
            first = await client.send_message("durable letter", to=["bob"], request_id="stable")
            # A fresh SQLite connection sees the mail as soon as HTTP returns.
            inbox = MailStore(database).mails("human:bob")
            assert [mail.id for mail in inbox] == [first["mail_id"]]
            assert first["local_recipients"] == ["human:bob"]
            assert await client.get_inbox(username="bob")
            assert len(MailStore(database).mails("human:bob", "unread")) == 1

    async with _server(tmp_path) as (url, _), TlonClient(url) as client:
        replay = await client.send_message("durable letter", to=["bob"], request_id="stable")
        assert replay == first
        assert [mail.id for mail in MailStore(database).mails("human:bob")] == [first["mail_id"]]


async def test_retired_and_unknown_addresses_refuse_entire_post(tmp_path: Path) -> None:
    async with _server(tmp_path) as (url, app):
        assert app._runtime is not None
        runtime = app._runtime
        async with aiohttp.ClientSession() as http:
            async with http.post(
                f"{url}/admin/config/agents",
                json={
                    "prototype": "developer",
                    "name": "退休测试员",
                    "model": "openai/gpt-test",
                    "agent_id": "retired-test",
                },
            ) as response:
                assert response.status == 200
            store = runtime.post_office.store
            history = Mail.create(
                from_address="guest@tlon.internal",
                to=["retired-test@tlon.internal"],
                content="history",
            )
            await asyncio.to_thread(store.add_to_inbox, history, "retired-test")
            await runtime.agent_registry.stop_member("retired-test", stopped_by="integration-test")
            async with http.get(f"{url}/inbox", params={"username": "bob"}) as response:
                assert response.status == 200
            async with http.post(
                f"{url}/messages",
                json={
                    "request_id": "refused",
                    "content": "must not partly arrive",
                    "to": ["bob", "retired-test", "unknown-human"],
                    "cc": ["also-unknown"],
                },
            ) as response:
                assert response.status == 400
                error: dict[str, Any] = await response.json()
            assert error["reason"] == "invalid_recipients"
            assert error["invalid_recipients"] == ["retired-test", "unknown-human", "also-unknown"]
            assert store.mails("human:bob") == []
            assert store.mails("human:guest", "sent") == []
            async with http.get(f"{url}/members/retired-test/mail") as response:
                assert response.status == 200
                assert [mail["id"] for mail in (await response.json())["mails"]] == [history.id]
