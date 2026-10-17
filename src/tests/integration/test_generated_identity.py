"""System IDs survive proposal/member creation, delivery and process restart."""

import re
from pathlib import Path

import aiohttp

from .conftest import WEBUI_HEADERS, _read_inbox, _send_mail
from .test_post_office import _server

BEHAVIOR = '{:version 1 :rules [{:name "receive" :on :mail-delivered :actions []}]}'


async def test_generated_ids_create_deliver_and_survive_restart(tmp_path: Path) -> None:
    async with _server(tmp_path) as (url, _), aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
        async with http.post(
            f"{url}/admin/prototypes",
            json={
                "display_name": "收到后静默",
                "description": "收到邮件后不执行动作",
                "behavior": BEHAVIOR,
            },
        ) as response:
            payload = await response.json()
            assert response.status == 201, payload
            prototype = payload["prototype"]
        prototype_id = prototype["name"]
        assert re.fullmatch(r"p-[0-9a-f]{32}", prototype_id)
        assert prototype["display_name"] == "收到后静默"
        async with http.post(
            f"{url}/admin/config/agents",
            json={
                "prototype": prototype_id,
                "name": "自由成员",
                "model": "openai/gpt-test",
            },
        ) as response:
            created = await response.json()
            assert response.status == 200, created
        member_id = created["agent_id"]
        assert re.fullmatch(r"m-[0-9a-f]{32}", member_id)
        assert prototype_id not in member_id
        receipt = await _send_mail(http, url, "opaque identifier smoke", to=[member_id])
        assert _read_inbox(tmp_path, member_id)[0]["id"] == receipt["mail_id"]
        async with http.post(
            f"{url}/admin/prototypes",
            json={
                "name": "developer",
                "display_name": "不应创建",
                "behavior": BEHAVIOR,
            },
        ) as response:
            assert response.status == 400
            assert (await response.json())["reason"] == "managed_id"
        async with http.post(
            f"{url}/admin/config/agents",
            json={
                "agent_id": "ceo",
                "prototype": prototype_id,
                "name": "不应创建",
                "model": "openai/gpt-test",
            },
        ) as response:
            assert response.status == 400
            assert (await response.json())["reason"] == "managed_id"
        assert not (tmp_path / "prototypes" / "developer").exists()
        assert not (tmp_path / "ceo").exists()

    async with _server(tmp_path) as (url, _), aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
        async with http.get(f"{url}/admin/prototypes/{prototype_id}") as response:
            assert response.status == 200
            assert (await response.json())["prototype"]["name"] == prototype_id
        async with http.get(f"{url}/members/{member_id}/mail") as response:
            assert response.status == 200
            assert (await response.json())["mails"][0]["content"] == "opaque identifier smoke"
