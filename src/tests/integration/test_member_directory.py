"""The runtime tool and DSL share durable member discovery and actual delivery."""

import asyncio
from pathlib import Path

import aiohttp

from tests.prototype_helpers import builtin_id
from tlon.agents.registry_queries import RegistryQueries
from tlon.lifecycle.ports import MemberDirectory
from tlon.lifecycle.service import LifecycleService
from tlon.operations.adapters import OperationTool
from tlon.operations.core import CallContext, OperationExecutor, OperationRegistry
from tlon.operations.lifecycle import make_lifecycle_operations

from .conftest import WEBUI_HEADERS, _read_inbox, _send_mail
from .test_post_office import _server


def _directory_tool(directory: MemberDirectory) -> OperationTool:
    executor = OperationExecutor(
        OperationRegistry(make_lifecycle_operations(LifecycleService("self", directory=directory))),
        CallContext("self", "test", "test"),
    )
    return OperationTool(executor, "member-list")


async def test_member_directory_matches_delivery_offline_and_after_restart(tmp_path: Path) -> None:
    async with _server(tmp_path) as (url, app):
        assert app._runtime is not None
        runtime = app._runtime
        assert runtime.members.list_ids() == []
        # Keep a real non-retired member across restart; empty directory checks
        # would lose the original durable-discovery coverage.
        async with (
            aiohttp.ClientSession(headers=WEBUI_HEADERS) as http,
            http.post(
                f"{url}/admin/config/agents",
                json={
                    "prototype": builtin_id("计划后执行"),
                    "name": "Carol",
                    "model": "openai/gpt-test",
                },
            ) as response,
        ):
            assert response.status == 200
            carol_id = (await response.json())["agent_id"]
        baseline_ids = set(runtime.members.list_ids())
        baseline = (
            await _directory_tool(directory=runtime.agent_registry).execute_context({})
        ).data
        assert baseline_ids == {carol_id}
        assert any(member["id"] == carol_id for member in baseline)
        async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
            async with http.post(
                f"{url}/admin/config/agents",
                json={
                    "prototype": builtin_id("计划后执行"),
                    "name": "Bob",
                    "model": "openai/gpt-test",
                },
            ) as response:
                assert response.status == 200
                bob_id = (await response.json())["agent_id"]
            # Alice has a durable mailbox but no online adapter/SSE session.
            assert runtime.events.list_online_humans() == []
            tool = _directory_tool(directory=runtime.agent_registry)
            result = await tool.execute_context({})
            assert result.success
            assert result.data == sorted(
                [
                    *baseline,
                    {"id": bob_id, "name": "Bob", "address": f"{bob_id}@tlon.internal"},
                ],
                key=lambda member: member["id"],
            )
            assert RegistryQueries(runtime.agent_registry).member_roster() == "\n".join(
                f"{member['name']}（{member['id']}） — {member['address']}"
                for member in result.data
            )
            report = await runtime.agent_registry._health.scan_orphan_mail()
            assert report["scanned_members"] == sorted(member["id"] for member in result.data)
            assert report["scanned_count"] == len(result.data)
            assert report["failed_members"] == []
            # Merely listing the unified directory does not change lifecycle targets.
            assert set(runtime.agent_registry.get_all_ids()) == baseline_ids | {bob_id}
            receipt = await _send_mail(http, url, "directory address is deliverable", to=["alice"])
            assert _read_inbox(tmp_path, "human:alice")[0]["id"] == receipt["mail_id"]
        await asyncio.to_thread(runtime.post_office.store.retire, bob_id)
        assert (await tool.execute_context({})).data == baseline
        assert (await runtime.agent_registry._health.scan_orphan_mail())[
            "scanned_members"
        ] == sorted(member["id"] for member in baseline)

    async with _server(tmp_path) as (_, restarted):
        assert restarted._runtime is not None
        tool = _directory_tool(directory=restarted._runtime.agent_registry)
        result = await tool.execute_context({})
        assert result.data == baseline
        assert all(set(member) == {"id", "name", "address"} for member in result.data)
        assert all("human:" not in member["id"] for member in result.data)
