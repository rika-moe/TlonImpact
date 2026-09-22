"""Integration tests for the SSE event stream (GET /events, protocol v2).

Opening the stream registers the session's event handler and attaches
it to the HumanAdapter, so mail addressed to the session's username is
fanned out onto the stream as ``data:`` frames. One stream per session;
a duplicate open is rejected with 409.
"""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import aiohttp

EVENT_TIMEOUT = 10.0


async def _create_session(http: aiohttp.ClientSession, base_url: str, username: str) -> str:
    """POST /sessions and return the new session id."""
    async with http.post(
        f"{base_url}/sessions",
        json={
            "username": username,
            "client_type": "test",
            "mode": "chat",
            "protocol_version": "2.0.0",
        },
    ) as resp:
        assert resp.status == 201
        data: dict[str, Any] = await resp.json()
    return str(data["session_id"])


async def _read_event_with_token(resp: aiohttp.ClientResponse, token: str) -> dict[str, Any]:
    """Scan SSE frames until a mail.delivered frame carries the token.

    Chat-mode sessions also see the pre-delivery mail.new frame for the
    same mail, so matching on the token alone is not enough — the
    adapter fan-out is the mail.delivered one.
    """
    async def _scan() -> dict[str, Any]:
        async for raw in resp.content:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue  # blank separators and ": ping" heartbeats
            frame: dict[str, Any] = json.loads(line[5:].strip())
            if frame.get("event_type") == "mail.delivered" and token in line:
                return frame
        raise AssertionError("SSE stream closed before the token arrived")

    return await asyncio.wait_for(_scan(), timeout=EVENT_TIMEOUT)


async def test_mail_delivered_pushed_to_sse_stream(inproc_server: tuple[str, Path]) -> None:
    """Mail to the session's username arrives as a mail.delivered frame."""
    base_url, _ = inproc_server
    token = f"sse-{uuid.uuid4().hex}"

    async with aiohttp.ClientSession() as http:
        session_id = await _create_session(http, base_url, "alice")
        try:
            async with http.get(
                f"{base_url}/events",
                params={"session_id": session_id},
                timeout=aiohttp.ClientTimeout(total=None),
            ) as stream:
                assert stream.status == 200
                assert stream.headers["Content-Type"].startswith("text/event-stream")

                # The stream is attached before its 200 headers arrive, so
                # alice is online and this send resolves her address.
                async with http.post(
                    f"{base_url}/messages",
                    json={"username": "guest", "content": token, "to": ["alice"]},
                ) as send_resp:
                    assert send_resp.status == 202

                frame = await _read_event_with_token(stream, token)
                assert frame["type"] == "event"
                assert frame["event_type"] == "mail.delivered"
                assert frame["payload"]["recipient"] == "human:alice"
                assert token in frame["payload"]["mail"]["content"]
        finally:
            # Closing the stream (context exit) detaches the session
            # server-side; DELETE is the belt-and-braces idempotent cleanup.
            async with http.delete(f"{base_url}/sessions/{session_id}") as resp:
                assert resp.status == 200


async def test_duplicate_stream_rejected_and_unknown_session_404(
    inproc_server: tuple[str, Path],
) -> None:
    """One stream per session (409 on a second open); unknown -> 404."""
    base_url, _ = inproc_server

    async with aiohttp.ClientSession() as http:
        async with http.get(
            f"{base_url}/events", params={"session_id": f"no-such-{uuid.uuid4().hex[:8]}"}
        ) as resp:
            assert resp.status == 404

        session_id = await _create_session(http, base_url, "alice")
        try:
            async with http.get(
                f"{base_url}/events",
                params={"session_id": session_id},
                timeout=aiohttp.ClientTimeout(total=None),
            ) as first:
                assert first.status == 200
                async with http.get(
                    f"{base_url}/events", params={"session_id": session_id}
                ) as second:
                    assert second.status == 409
                    data: dict[str, Any] = await second.json()
                    assert data["reason"] == "stream_active"
        finally:
            async with http.delete(f"{base_url}/sessions/{session_id}") as resp:
                assert resp.status == 200
