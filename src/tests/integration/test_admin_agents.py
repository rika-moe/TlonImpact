"""Integration tests for the admin agent config CRUD endpoints.

Covers GET/POST/PATCH/DELETE /admin/config/agents: a create -> list ->
update -> delete lifecycle over the hot-adopt path, plus the validation
branches (missing fields, unknown prototype, unknown agent). Presence is
asserted both through GET /admin/config/agents and on the filesystem
(the member home under TLON_HOME). The DELETE -> "home directory gone"
assertion relies on RetirementCeremony skipping administrative removals
(``removed=True``) instead of resurrecting the deleted home.
"""

import uuid
from pathlib import Path
from typing import Any

import aiohttp


async def test_agent_config_crud_lifecycle(inproc_server: tuple[str, Path]) -> None:
    """create -> patched -> deleted -> home directory gone."""
    base_url, tlon_home = inproc_server
    name = f"测试员-{uuid.uuid4().hex[:8]}"

    async with aiohttp.ClientSession() as http:
        async with http.post(
            f"{base_url}/admin/config/agents",
            json={"prototype": "developer", "name": name, "model": "openai/gpt-test"},
        ) as resp:
            assert resp.status == 200
            created: dict[str, Any] = await resp.json()
        agent_id = created["agent_id"]
        assert created["status"] == "success"
        assert created["name"] == name
        assert created["prototype"] == "developer"

        config_dir = tlon_home / agent_id / "config"
        assert (config_dir / "agent.yaml").is_file()

        async with http.get(f"{base_url}/admin/config/agents") as resp:
            assert resp.status == 200
            listed: dict[str, Any] = await resp.json()
        match = [a for a in listed["agents"] if a["id"] == agent_id]
        assert match == [{"id": agent_id, "name": name, "prototype": "developer"}]

        try:
            async with http.patch(
                f"{base_url}/admin/config/agents/{agent_id}",
                json={"persona": {"identity": "integration-test persona"}},
            ) as resp:
                assert resp.status == 200
                updated: dict[str, Any] = await resp.json()
            assert updated["status"] == "success"
            assert updated["agent_id"] == agent_id
            assert "integration-test persona" in (config_dir / "IDENTITY.md").read_text()
        finally:
            async with http.delete(f"{base_url}/admin/config/agents/{agent_id}") as resp:
                assert resp.status == 200
                deleted: dict[str, Any] = await resp.json()
                assert deleted["removed"] is True

        assert not (tlon_home / agent_id).exists()

        async with http.get(f"{base_url}/admin/config/agents") as resp:
            assert resp.status == 200
            after: dict[str, Any] = await resp.json()
        assert agent_id not in {a["id"] for a in after["agents"]}


async def test_agent_config_validation(inproc_server: tuple[str, Path]) -> None:
    """Validation branches: missing fields, unknown prototype/agent."""
    base_url, _ = inproc_server
    bogus_id = f"m-nope-{uuid.uuid4().hex[:8]}"

    async with aiohttp.ClientSession() as http:
        # Missing name -> 400
        async with http.post(
            f"{base_url}/admin/config/agents", json={"prototype": "developer"}
        ) as resp:
            assert resp.status == 400

        # Missing prototype -> 400
        async with http.post(f"{base_url}/admin/config/agents", json={"name": "无名氏"}) as resp:
            assert resp.status == 400

        # Unknown prototype -> 400 (PrototypeError from instantiate)
        async with http.post(
            f"{base_url}/admin/config/agents",
            json={"prototype": "no-such-prototype", "name": "无名氏", "model": "openai/gpt-test"},
        ) as resp:
            assert resp.status == 400

        # Missing model -> 400 (ADR 0016: no process-level fallback)
        async with http.post(
            f"{base_url}/admin/config/agents",
            json={"prototype": "developer", "name": "无名氏"},
        ) as resp:
            assert resp.status == 400

        # PATCH unknown agent -> 404
        async with http.patch(
            f"{base_url}/admin/config/agents/{bogus_id}", json={"persona": {}}
        ) as resp:
            assert resp.status == 404

        # DELETE unknown agent -> 409 (StopRejectedError: unknown_agent)
        async with http.delete(f"{base_url}/admin/config/agents/{bogus_id}") as resp:
            assert resp.status == 409
            data: dict[str, Any] = await resp.json()
            assert data["reason"] == "unknown_agent"
