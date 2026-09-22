"""Integration tests for admin token auth (ADR 0013, TLON_ADMIN_TOKEN).

With TLON_ADMIN_TOKEN configured, every /admin/* request must carry
``Authorization: Bearer <token>``; non-admin endpoints stay open. The
default (token unset) behavior — admin endpoints open — is covered by
test_admin_agents.py against a server without TLON_ADMIN_TOKEN.
"""

import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from .conftest import _run_inprocess

ADMIN_TOKEN = f"test-token-{uuid.uuid4().hex}"


@pytest.fixture
async def authed_server() -> AsyncGenerator[tuple[str, Path]]:
    """An in-process server with TLON_ADMIN_TOKEN configured."""
    async with _run_inprocess({"TLON_ADMIN_TOKEN": ADMIN_TOKEN}) as server:
        yield server


async def test_admin_requires_bearer_token(authed_server: tuple[str, Path]) -> None:
    """No/wrong credentials -> 401; correct Bearer token -> 200."""
    base_url, _ = authed_server

    async with aiohttp.ClientSession() as http:
        async with http.get(f"{base_url}/admin/prototypes") as resp:
            assert resp.status == 401
            data: dict[str, Any] = await resp.json()
            assert data["reason"] == "unauthorized"

        async with http.get(
            f"{base_url}/admin/prototypes",
            headers={"Authorization": "Bearer wrong-token"},
        ) as resp:
            assert resp.status == 401

        async with http.get(
            f"{base_url}/admin/prototypes",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        ) as resp:
            assert resp.status == 200


async def test_non_admin_endpoints_stay_open(authed_server: tuple[str, Path]) -> None:
    """The mail/health surface keeps the name-is-identity model (no auth)."""
    base_url, _ = authed_server

    async with aiohttp.ClientSession() as http:
        async with http.get(f"{base_url}/health") as resp:
            assert resp.status == 200
        async with http.get(f"{base_url}/inbox", params={"username": "alice"}) as resp:
            assert resp.status == 200


async def test_options_preflight_passes_without_credentials(
    authed_server: tuple[str, Path],
) -> None:
    """CORS preflight carries no credentials by design and must pass."""
    base_url, _ = authed_server

    async with (
        aiohttp.ClientSession() as http,
        http.options(f"{base_url}/admin/prototypes") as resp,
    ):
        assert resp.status == 200
        assert "Authorization" in resp.headers["Access-Control-Allow-Headers"]
