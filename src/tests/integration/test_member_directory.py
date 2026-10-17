"""The runtime tool and DSL share durable member discovery and actual delivery."""

import asyncio
from pathlib import Path

import aiohttp

from tlon.agents.agent_registry import RegistryQueries
from tlon.tools.agents.list_members import ListMembersTool

from .conftest import WEBUI_HEADERS, _read_inbox, _send_mail
from .test_post_office import _server


async def test_member_directory_matches_delivery_offline_and_after_restart(tmp_path: Path) -> None:
    async with _server(tmp_path) as (url, app):
        assert app._runtime is not None
        runtime = app._runtime
        async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
            async with http.post(
                f"{url}/admin/config/agents",
                json={
                    "prototype": "developer",
                    "name": "Bob",
                    "model": "openai/gpt-test",
                    "agent_id": "bob",
                },
            ) as response:
                assert response.status == 200
            # Alice has a durable mailbox but no online adapter/SSE session.
            assert runtime.events.list_online_humans() == []
            tool = ListMembersTool(directory=runtime.agent_registry)
            result = await tool.execute()
            assert result.success
            assert result.data == [
                {"id": "adam", "name": "Adam", "address": "adam@tlon.internal"},
                {"id": "alice", "name": "alice", "address": "alice@tlon.internal"},
                {"id": "bob", "name": "Bob", "address": "bob@tlon.internal"},
            ]
            assert RegistryQueries(runtime.agent_registry).member_roster() == "\n".join(
                f"{member['name']}（{member['id']}） — {member['address']}"
                for member in result.data
            )
            # Merely listing the unified directory does not change lifecycle targets.
            assert set(runtime.agent_registry.get_all_ids()) == {"bob"}
            receipt = await _send_mail(http, url, "directory address is deliverable", to=["alice"])
            assert _read_inbox(tmp_path, "human:alice")[0]["id"] == receipt["mail_id"]
        await asyncio.to_thread(runtime.post_office.store.retire, "bob")
        assert [member["id"] for member in (await tool.execute()).data] == ["adam", "alice"]

    async with _server(tmp_path) as (_, restarted):
        assert restarted._runtime is not None
        tool = ListMembersTool(directory=restarted._runtime.agent_registry)
        result = await tool.execute()
        assert [member["id"] for member in result.data] == ["adam", "alice"]
        assert all(set(member) == {"id", "name", "address"} for member in result.data)
        assert all("human:" not in member["id"] for member in result.data)
