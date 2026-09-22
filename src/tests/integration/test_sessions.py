"""Integration tests for the session lifecycle (protocol v2).

Covers POST/PATCH/DELETE /sessions and GET /sessions: the happy path
goes through the SDK (authenticate + set_mode + disconnect), the edge
cases (bad protocol version, unknown session, invalid mode, idempotent
delete) go through raw aiohttp calls.
"""

import asyncio
import uuid
from pathlib import Path
from typing import Any

import aiohttp

from tlon_client.sdk.client import TlonClient

SESSIONS_TIMEOUT = 10.0


async def _session_ids(http: aiohttp.ClientSession, base_url: str) -> list[str]:
    """Return the ids of the sessions the server currently lists."""
    async with http.get(f"{base_url}/sessions") as resp:
        assert resp.status == 200
        data: dict[str, Any] = await resp.json()
    return [s["id"] for s in data["sessions"]]


async def test_sdk_session_lifecycle(tlon_server: tuple[str, Path]) -> None:
    """SDK authenticate -> set_mode -> disconnect drives POST/PATCH/DELETE."""
    base_url, _ = tlon_server

    async with TlonClient(base_url=base_url) as client:
        assert await client.authenticate(username="alice")
        session_id = client.session_id
        assert session_id is not None

        async with aiohttp.ClientSession() as http:
            assert session_id in await _session_ids(http, base_url)

        assert await client.set_mode("event")
        assert client.state.name == "AUTHENTICATED"

    # __aexit__ disconnected: the session is deleted server-side (the
    # DELETE and the SSE-teardown cleanup race; both are idempotent).
    async with aiohttp.ClientSession() as http:
        deadline = asyncio.get_running_loop().time() + SESSIONS_TIMEOUT
        while session_id in await _session_ids(http, base_url):
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("session still listed after disconnect")
            await asyncio.sleep(0.05)


async def test_session_edge_cases(tlon_server: tuple[str, Path]) -> None:
    """Raw-HTTP edge cases: validation errors and idempotent delete."""
    base_url, _ = tlon_server
    suffix = uuid.uuid4().hex[:8]

    async with aiohttp.ClientSession() as http:
        # Wrong protocol version -> 400 unsupported_protocol_version
        async with http.post(
            f"{base_url}/sessions",
            json={
                "username": "alice",
                "client_type": "test",
                "mode": "chat",
                "protocol_version": "1.0.0",
            },
        ) as resp:
            assert resp.status == 400
            data = await resp.json()
            assert data["reason"] == "unsupported_protocol_version"

        # PATCH an unknown session -> 404
        async with http.patch(
            f"{base_url}/sessions/no-such-{suffix}", json={"mode": "chat"}
        ) as resp:
            assert resp.status == 404

        # DELETE is idempotent: unknown session -> 200
        async with http.delete(f"{base_url}/sessions/no-such-{suffix}") as resp:
            assert resp.status == 200

        # Valid create -> 201 with the session state + snapshots
        async with http.post(
            f"{base_url}/sessions",
            json={
                "username": "alice",
                "client_type": "test",
                "mode": "chat",
                "protocol_version": "2.0.0",
            },
        ) as resp:
            assert resp.status == 201
            created: dict[str, Any] = await resp.json()
        session_id = created["session_id"]
        assert created["username"] == "alice"
        assert created["mode"] == "chat"
        assert isinstance(created["participants"], list)
        assert isinstance(created["agents"], list)

        # Listed
        assert session_id in await _session_ids(http, base_url)

        # PATCH mode + filters -> 200 with the updated state
        async with http.patch(
            f"{base_url}/sessions/{session_id}",
            json={"mode": "event", "filters": {"event_types": ["mail.delivered"]}},
        ) as resp:
            assert resp.status == 200
            updated: dict[str, Any] = await resp.json()
        assert updated["mode"] == "event"
        assert updated["filters"] == {"event_types": ["mail.delivered"]}

        # PATCH invalid mode -> 400 invalid_mode
        async with http.patch(
            f"{base_url}/sessions/{session_id}", json={"mode": "bogus"}
        ) as resp:
            assert resp.status == 400
            data = await resp.json()
            assert data["reason"] == "invalid_mode"

        # DELETE -> 200, and the session leaves the listing
        async with http.delete(f"{base_url}/sessions/{session_id}") as resp:
            assert resp.status == 200
        assert session_id not in await _session_ids(http, base_url)

        # DELETE again -> still 200 (idempotent)
        async with http.delete(f"{base_url}/sessions/{session_id}") as resp:
            assert resp.status == 200
