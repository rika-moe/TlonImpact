"""Real-HTTP regression coverage for #144's local browser trust boundary."""

from pathlib import Path

import aiohttp

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
