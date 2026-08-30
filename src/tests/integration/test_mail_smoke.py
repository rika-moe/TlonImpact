"""End-to-end mail smoke test against a real ``tlon dream`` subprocess.

Automates the manual smoke flow from AGENTS.md: boot the server with an
isolated TLON_HOME, send mail through the SDK, assert it lands in the
recipient's inbox.jsonl. The delivery path (POST /messages -> event bus
-> MailDelivery) touches neither LLM nor Docker, so it runs in plain CI.
"""

import asyncio
import os
import socket
import tempfile
import uuid
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

import aiohttp
import pytest

from tlon.app.clients.sdk.client import TlonClient

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


async def test_mail_to_agent_lands_in_inbox(tlon_server: tuple[str, Path]) -> None:
    """POST /messages -> event bus -> MailDelivery -> agent inbox.jsonl."""
    base_url, tlon_home = tlon_server
    token = f"smoke-{uuid.uuid4().hex}"

    async with TlonClient(base_url=base_url) as client:
        accepted = await client.send_message(token, to=["lin-zhiyao"], subject="smoke")

    assert accepted
    await _assert_delivered(tlon_home, "lin-zhiyao", token)


async def test_mail_to_known_human_lands_in_inbox(tlon_server: tuple[str, Path]) -> None:
    """Mail to a known human waits in their inbox (human -> human path)."""
    base_url, tlon_home = tlon_server
    token = f"smoke-{uuid.uuid4().hex}"

    async with TlonClient(base_url=base_url) as client:
        # Reading bob's inbox creates his mailbox, making him a known human.
        await client.get_inbox(username="bob")
        accepted = await client.send_message(token, to=["bob"], subject="smoke")

    assert accepted
    await _assert_delivered(tlon_home, "human-bob", token)
