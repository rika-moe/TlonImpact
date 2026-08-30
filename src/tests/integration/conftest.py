"""Shared fixtures and helpers for the integration suite.

Each test boots a real ``tlon dream`` subprocess with an isolated
TLON_HOME (function scope — isolation over speed). Helpers cover the
common flows: waiting on conditions, asserting mail delivery, and
reading a member's inbox.jsonl.
"""

import asyncio
import json
import os
import socket
import tempfile
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any

import aiohttp
import pytest

SERVER_STARTUP_TIMEOUT = 45.0
DELIVERY_TIMEOUT = 10.0


def _free_port() -> int:
    """Grab an ephemeral port by binding 0 and releasing it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_for(predicate: Callable[[], bool], timeout: float) -> None:
    """Poll until the predicate holds or the timeout expires."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("condition not met in time")
        await asyncio.sleep(0.05)


async def _wait_ready(
    base_url: str,
    proc: asyncio.subprocess.Process,
    server_output: Callable[[], str],
) -> None:
    """Poll GET /inbox until the REST API answers 200.

    The probe read also creates the "smoke" mailbox — harmless in the
    throwaway TLON_HOME.
    """
    deadline = asyncio.get_running_loop().time() + SERVER_STARTUP_TIMEOUT
    async with aiohttp.ClientSession() as session:
        while True:
            if proc.returncode is not None:
                raise RuntimeError(
                    f"tlon dream exited with {proc.returncode} during startup:\n{server_output()}"
                )
            try:
                async with session.get(f"{base_url}/inbox", params={"username": "smoke"}) as resp:
                    if resp.status == 200:
                        return
            except aiohttp.ClientError:
                pass  # Server not listening yet
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError(
                    f"tlon dream not ready within {SERVER_STARTUP_TIMEOUT}s:\n{server_output()}"
                )
            await asyncio.sleep(0.25)


@pytest.fixture
async def tlon_server() -> AsyncGenerator[tuple[str, Path]]:
    """Boot a real ``tlon dream`` subprocess with an isolated TLON_HOME.

    Yields (base_url, tlon_home). The subprocess output is drained
    continuously so a startup failure can report what the server said.
    """
    port = _free_port()
    output: list[str] = []

    def _server_output() -> str:
        return "".join(output[-200:])

    with tempfile.TemporaryDirectory(prefix="tlon-smoke-") as tlon_home:
        proc = await asyncio.create_subprocess_exec(
            "uv",
            "run",
            "tlon",
            "dream",
            "--host",
            "127.0.0.1",
            "--rest-port",
            str(port),
            env={**os.environ, "TLON_HOME": tlon_home},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async def _drain() -> None:
            assert proc.stdout is not None
            async for chunk in proc.stdout:
                output.append(chunk.decode(errors="replace"))

        drain = asyncio.create_task(_drain())
        try:
            base_url = f"http://127.0.0.1:{port}"
            await _wait_ready(base_url, proc, _server_output)
            yield base_url, Path(tlon_home)
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10.0)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
            await drain


async def _assert_delivered(tlon_home: Path, member_dir: str, token: str) -> None:
    """Wait for a mail carrying the token to land in the member's inbox."""
    inbox = tlon_home / member_dir / "mail" / "inbox.jsonl"
    await _wait_for(lambda: inbox.exists() and token in inbox.read_text(), timeout=DELIVERY_TIMEOUT)


def _read_inbox(tlon_home: Path, member_dir: str) -> list[dict[str, Any]]:
    """Parse a member's inbox.jsonl into mail dicts (empty if absent)."""
    inbox = tlon_home / member_dir / "mail" / "inbox.jsonl"
    if not inbox.exists():
        return []
    return [json.loads(line) for line in inbox.read_text().splitlines() if line.strip()]


def _find_inbox_mail(tlon_home: Path, member_dir: str, token: str) -> dict[str, Any]:
    """Return the inbox mail whose content carries the token."""
    for mail in _read_inbox(tlon_home, member_dir):
        if token in mail.get("content", ""):
            return mail
    raise AssertionError(f"no mail with token {token!r} in {member_dir}'s inbox")
