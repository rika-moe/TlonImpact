"""Real-HTTP regression coverage for #144's local browser trust boundary."""

from pathlib import Path

import aiohttp

from tests.prototype_helpers import builtin_id

from .conftest import WEBUI_HEADERS


async def test_mail_and_admin_share_browser_boundary(inproc_server: tuple[str, Path]) -> None:
    base_url, _ = inproc_server
    async with aiohttp.ClientSession() as http:
        for path in ("/inbox", "/admin/prototypes", "/identity", "/logs"):
            async with http.get(f"{base_url}{path}") as response:
                assert response.status == 403
                assert "Access-Control-Allow-Origin" not in response.headers
            async with http.get(f"{base_url}{path}", headers=WEBUI_HEADERS) as response:
                assert response.status == 200
                assert "Access-Control-Allow-Origin" not in response.headers
        async with http.get(f"{base_url}/health") as response:
            assert response.status == 200


async def test_evil_site_cannot_read_send_or_preflight(inproc_server: tuple[str, Path]) -> None:
    base_url, _ = inproc_server
    async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
        async with http.get(
            f"{base_url}/inbox", headers={"Origin": "https://evil.example"}
        ) as response:
            assert response.status == 403
        async with http.post(
            f"{base_url}/messages",
            headers={"Origin": "https://evil.example"},
            json={"content": "forged", "to": ["alice"], "request_id": "forged"},
        ) as response:
            assert response.status == 403
        async with http.options(
            f"{base_url}/messages",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Headers": "X-Tlon-WebUI",
                "Access-Control-Request-Method": "POST",
            },
        ) as response:
            assert response.status == 403
            assert "Access-Control-Allow-Origin" not in response.headers
        async with http.get(f"{base_url}/inbox") as response:
            assert (await response.json())["mails"] == []


async def test_new_browser_uses_instance_identity_and_cannot_select_sender(
    inproc_server: tuple[str, Path],
) -> None:
    base_url, _ = inproc_server
    for _ in range(2):
        async with (
            aiohttp.ClientSession(headers=WEBUI_HEADERS) as http,
            http.get(f"{base_url}/identity") as response,
        ):
            assert await response.json() == {
                "username": "alice",
                "address": "alice@tlon.internal",
            }
    async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
        async with http.post(f"{base_url}/identity", json={"username": "mallory"}) as response:
            assert response.status == 409
        async with http.get(f"{base_url}/inbox", params={"username": "mallory"}) as response:
            assert response.status == 400
            assert (await response.json())["reason"] == "identity_managed"
        async with http.post(
            f"{base_url}/messages",
            json={
                "username": "mallory",
                "content": "forged",
                "to": ["alice"],
                "request_id": "forged",
            },
        ) as response:
            assert response.status == 400
            assert (await response.json())["reason"] == "identity_managed"


async def test_patch_cannot_adopt_offline_member_at_operator_address(
    inproc_server: tuple[str, Path],
) -> None:
    from tlon.agents.prototypes.instantiate import instantiate
    from tlon.base.paths import TlonPaths

    from .conftest import _read_inbox, _send_mail

    base_url, home = inproc_server
    paths = TlonPaths(home)
    instantiate(
        builtin_id("标准工具迭代"),
        "alice",
        "Offline member",
        model="openai/gpt-test",
        tlon_paths=paths,
    )
    original_config = paths.agent_config_file("alice").read_bytes()
    async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
        async with http.patch(
            f"{base_url}/admin/config/agents/alice",
            json={"persona": {"identity": "must not be written"}, "llm": {"model_name": "other"}},
        ) as response:
            assert response.status == 409
            assert (await response.json())["reason"] == "identity_conflict"
        assert paths.agent_config_file("alice").read_bytes() == original_config
        assert not paths.agent_identity_file("alice").exists()
        async with http.get(f"{base_url}/agents") as response:
            assert "alice" not in {agent["id"] for agent in (await response.json())["agents"]}
        receipt = await _send_mail(http, base_url, "operator still owns this address", to=["alice"])
    inbox = _read_inbox(home, "human:alice")
    assert [mail["id"] for mail in inbox] == [receipt["mail_id"]]
    assert _read_inbox(home, "alice") == []
