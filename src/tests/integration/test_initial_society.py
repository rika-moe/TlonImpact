"""Packaged initial society reaches the real server without LLM or Docker work."""

import re
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from tlon.base.paths import TlonPaths
from tlon.organization.mail.store import MailStore

from .conftest import WEBUI_HEADERS
from .test_post_office import _server


async def _list_agents(base_url: str) -> list[dict[str, Any]]:
    async with (
        aiohttp.ClientSession(headers=WEBUI_HEADERS) as http,
        http.get(f"{base_url}/admin/config/agents") as response,
    ):
        assert response.status == 200
        return (await response.json())["agents"]


@pytest.mark.full_initial_society
async def test_initial_society_served_with_local_contacts_and_preserved_on_restart(
    tmp_path: Path,
) -> None:
    """Two real homes receive independent addresses; restarts preserve human edits.

    No letter is sent to a seeded member: bootstrapping and HTTP discovery
    must work without a model call, an API key, or a sandbox container.
    """
    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    async with _server(home_a) as (url, app):
        assert app._runtime is not None
        members = await _list_agents(url)
        assert len(members) == 30
        ids_a = {member["id"] for member in members}
        assert len(ids_a) == 30
        assert {"孔子", "屠呦呦", "老子", "孙武"} <= {member["name"] for member in members}
        for member_id in ids_a:
            config = app._runtime.members.get_config(member_id)
            assert config is not None
            assert config.llm is not None
            assert config.llm.model_name == "deepseek/deepseek-flash"
            config_dir = TlonPaths(home_a).agent_config_dir(member_id)
            for filename in ("IDENTITY.md", "SOUL.md", "RULES.md"):
                assert re.search(r"[\u4e00-\u9fff]", (config_dir / filename).read_text())
            contacts = TlonPaths(home_a).agent_contacts_file(member_id).read_text()
            peers = re.findall(r"(m-[0-9a-f]+)@tlon\.internal", contacts)
            assert 2 <= len(peers) <= 4
            assert len(peers) == len(set(peers))
            assert set(peers) <= ids_a - {member_id}
            assert MailStore(TlonPaths(home_a).mail_database_file()).mails(member_id) == []
        edited_id = members[0]["id"]
        async with (
            aiohttp.ClientSession(headers=WEBUI_HEADERS) as http,
            http.patch(
                f"{url}/admin/config/agents/{edited_id}",
                json={"persona": {"identity": "# 我自己修改的身份\n\n这段内容必须保留。\n"}},
            ) as response,
        ):
            assert response.status == 200

    async with _server(home_b) as (url, _):
        ids_b = {member["id"] for member in await _list_agents(url)}
        assert len(ids_b) == 30
        assert ids_a.isdisjoint(ids_b)
        for member_id in ids_b:
            contacts = TlonPaths(home_b).agent_contacts_file(member_id).read_text()
            assert set(re.findall(r"(m-[0-9a-f]+)@tlon\.internal", contacts)) <= ids_b

    async with _server(home_a) as (url, _):
        assert {member["id"] for member in await _list_agents(url)} == ids_a
        identity = (TlonPaths(home_a).agent_config_dir(edited_id) / "IDENTITY.md").read_text()
        assert identity == "# 我自己修改的身份\n\n这段内容必须保留。\n"
