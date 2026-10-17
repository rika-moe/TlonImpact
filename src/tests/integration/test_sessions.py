"""Integration tests for the session lifecycle (protocol v2).

Covers WebUI POST/PATCH/DELETE /sessions and GET /sessions, including
protocol validation, snapshots, invalid modes and idempotent deletion.
"""

import uuid
from pathlib import Path
from typing import Any

import aiohttp

from .conftest import WEBUI_HEADERS

SESSIONS_TIMEOUT = 10.0


async def _session_ids(http: aiohttp.ClientSession, base_url: str) -> list[str]:
    """Return the ids of the sessions the server currently lists."""
    async with http.get(f"{base_url}/sessions") as resp:
        assert resp.status == 200
        data: dict[str, Any] = await resp.json()
    return [s["id"] for s in data["sessions"]]


async def test_session_edge_cases(inproc_server: tuple[str, Path]) -> None:
    """Raw-HTTP edge cases: validation errors and idempotent delete."""
    base_url, _ = inproc_server
    suffix = uuid.uuid4().hex[:8]

    async with aiohttp.ClientSession(headers=WEBUI_HEADERS) as http:
        # Wrong protocol version -> 400 unsupported_protocol_version
        async with http.post(
            f"{base_url}/sessions",
            json={
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
        async with http.patch(f"{base_url}/sessions/{session_id}", json={"mode": "bogus"}) as resp:
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
