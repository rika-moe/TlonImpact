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

from tests.prototype_helpers import builtin_id

from .conftest import WEBUI_HEADERS


async def test_agent_config_crud_lifecycle(inproc_server: tuple[str, Path]) -> None:
    """create -> patched -> deleted -> home directory gone."""
    base_url, tlon_home = inproc_server
    name = f"测试员-{uuid.uuid4().hex[:8]}"

    async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
        async with http.post(
            f"{base_url}/admin/config/agents",
            json={
                "prototype": builtin_id("计划后执行"),
                "name": name,
                "model": "openai/gpt-test",
            },
        ) as resp:
            assert resp.status == 200
            created: dict[str, Any] = await resp.json()
        agent_id = created["agent_id"]
        assert created["status"] == "success"
        assert created["name"] == name
        assert created["prototype"] == builtin_id("计划后执行")

        config_dir = tlon_home / agent_id / "config"
        assert (config_dir / "agent.yaml").is_file()

        async with http.get(f"{base_url}/admin/config/agents") as resp:
            assert resp.status == 200
            listed: dict[str, Any] = await resp.json()
        match = [a for a in listed["agents"] if a["id"] == agent_id]
        assert match == [{"id": agent_id, "name": name, "prototype": builtin_id("计划后执行")}]

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

    async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
        # Missing name -> 400
        async with http.post(
            f"{base_url}/admin/config/agents", json={"prototype": builtin_id("计划后执行")}
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
            json={"prototype": builtin_id("计划后执行"), "name": "无名氏"},
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


async def test_self_file_read_and_patch(inproc_server: tuple[str, Path]) -> None:
    """Admin edits preserve full Markdown, omitted files and explicit empty values."""
    base_url, home = inproc_server
    original = {"identity": "# 自我\n\n我是研究者。\n", "profile": "重视证据", "behavior": "附来源"}
    async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
        async with http.post(
            f"{base_url}/admin/config/agents",
            json={
                "prototype": builtin_id("计划后执行"),
                "name": "自我文件测试",
                "model": "openai/gpt-test",
                "persona": original,
            },
        ) as response:
            assert response.status == 200
            agent_id = (await response.json())["agent_id"]
        url = f"{base_url}/admin/config/agents/{agent_id}"
        async with http.get(url) as response:
            assert response.status == 200
            data = await response.json()
            assert data["persona"] == original
            assert data["llm"] == {"model_name": "openai/gpt-test"}
        async with http.patch(url, json={"persona": {"profile": ""}}) as response:
            assert response.status == 200
        async with http.get(url) as response:
            assert (await response.json())["persona"] == {**original, "profile": ""}
        assert (home / agent_id / "config" / "SOUL.md").read_text() == ""
        (home / agent_id / "config" / "RULES.md").unlink()
        async with http.get(url) as response:
            assert (await response.json())["persona"]["behavior"] == ""
        for body in [
            [],
            {"persona": None},
            {"persona": []},
            {"persona": {"identity": 1}},
            {"persona": {"identity": "changed", "unknown": "bad"}},
            {"persona": {"identity": "changed"}, "llm": "bad"},
        ]:
            async with http.patch(url, json=body) as response:
                assert response.status == 400
        async with http.get(url) as response:
            assert (await response.json())["persona"]["identity"] == original["identity"]
        async with http.get(f"{base_url}/admin/config/agents/m-missing") as response:
            assert response.status == 404
