"""The runtime tool and DSL share durable member discovery and actual delivery."""

import asyncio
from pathlib import Path

import aiohttp

from tests.prototype_helpers import builtin_id
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
                    "prototype": builtin_id("长预算工具迭代"),
                    "name": "Bob",
                    "model": "openai/gpt-test",
                },
            ) as response:
                assert response.status == 200
                bob_id = (await response.json())["agent_id"]
            # Alice has a durable mailbox but no online adapter/SSE session.
            assert runtime.events.list_online_humans() == []
            tool = ListMembersTool(directory=runtime.agent_registry)
            result = await tool.execute()
            assert result.success
            assert result.data == [
                {"id": "adam", "name": "Adam", "address": "adam@tlon.internal"},
                {"id": "alice", "name": "alice", "address": "alice@tlon.internal"},
                {"id": bob_id, "name": "Bob", "address": f"{bob_id}@tlon.internal"},
            ]
            assert RegistryQueries(runtime.agent_registry).member_roster() == "\n".join(
                f"{member['name']}（{member['id']}） — {member['address']}"
                for member in result.data
            )
            report = await runtime.agent_registry._health.scan_orphan_mail()
            assert report["scanned_members"] == sorted(member["id"] for member in result.data)
            assert report["scanned_count"] == len(result.data)
            assert report["failed_members"] == []
            # Merely listing the unified directory does not change lifecycle targets.
            assert set(runtime.agent_registry.get_all_ids()) == {bob_id}
            receipt = await _send_mail(http, url, "directory address is deliverable", to=["alice"])
            assert _read_inbox(tmp_path, "human:alice")[0]["id"] == receipt["mail_id"]
        await asyncio.to_thread(runtime.post_office.store.retire, bob_id)
        assert [member["id"] for member in (await tool.execute()).data] == ["adam", "alice"]
        assert (await runtime.agent_registry._health.scan_orphan_mail())["scanned_members"] == [
            "adam",
            "alice",
        ]

    async with _server(tmp_path) as (_, restarted):
        assert restarted._runtime is not None
        tool = ListMembersTool(directory=restarted._runtime.agent_registry)
        result = await tool.execute()
        assert [member["id"] for member in result.data] == ["adam", "alice"]
        assert all(set(member) == {"id", "name", "address"} for member in result.data)
        assert all("human:" not in member["id"] for member in result.data)
