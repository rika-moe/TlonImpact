"""Shared fixtures and helpers for the integration suite.

Two server fixtures, two jobs:

- ``inproc_server`` (default): drives ``ServerApp`` in-process over real
  HTTP on an ephemeral 127.0.0.1 port, with an isolated TLON_HOME per
  test (function scope — isolation over speed). Fast; the workhorse for
  every protocol test.
- ``tlon_server``: boots a real ``tlon dream`` subprocess. Kept as the
  standing pin for the launcher/CLI path and the process boundary
  (test_mail_smoke.py).

Helpers cover the common flows: waiting on conditions, asserting mail
delivery, and reading a member's SQLite inbox.
"""

import asyncio
import contextlib
import logging
import os
import signal
import socket
import tempfile
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiohttp
import litellm
import pytest

from tlon.base.paths import TlonPaths
from tlon.base.settings import Settings
from tlon.organization.mail.store import MailStore
from tlon_server.serverapp import ServerApp

SERVER_STARTUP_TIMEOUT = 45.0
SERVER_SHUTDOWN_TIMEOUT = 15.0
SERVER_TERMINATE_TIMEOUT = 10.0
SERVER_KILL_TIMEOUT = 5.0
logger = logging.getLogger(__name__)
TEST_MODEL_OUTPUT_TOKENS = 8192
TEST_MODEL_CONTEXT_WINDOW = 32768


@pytest.fixture(autouse=True)
def fictional_model_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the integration-only model explicit capabilities in every server fixture."""
    real_model_info = litellm.get_model_info

    def model_info(model: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if model == "openai/gpt-test":
            return {
                "max_output_tokens": TEST_MODEL_OUTPUT_TOKENS,
                "max_input_tokens": TEST_MODEL_CONTEXT_WINDOW,
            }
        return real_model_info(model, *args, **kwargs)

    monkeypatch.setattr(litellm, "get_model_info", model_info)


def _free_port() -> int:
    """Grab an ephemeral port by binding 0 and releasing it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_ready(
    base_url: str,
    proc: asyncio.subprocess.Process,
    server_output: Callable[[], str],
) -> None:
    """Poll GET /health until the REST API answers 200."""
    deadline = asyncio.get_running_loop().time() + SERVER_STARTUP_TIMEOUT
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2.0)) as session:
        while True:
            if proc.returncode is not None:
                raise RuntimeError(
                    f"tlon dream exited with {proc.returncode} during startup:\n{server_output()}"
                )
            try:
                async with session.get(f"{base_url}/health") as resp:
                    if resp.status == 200:
                        return
            except (aiohttp.ClientError, TimeoutError):
                pass  # Server not listening yet
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError(
                    f"tlon dream not ready within {SERVER_STARTUP_TIMEOUT}s:\n{server_output()}"
                )
            await asyncio.sleep(0.25)


async def _stop_server_process(
    proc: asyncio.subprocess.Process,
    drain: asyncio.Task[None],
    server_output: Callable[[], str],
) -> None:
    """Reap the CLI process group, including descendants holding its output pipe."""

    def signal_group(sig: signal.Signals) -> None:
        # The group may outlive uv; proc.returncode cannot prove it is gone.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, sig)

    async def finish() -> None:
        await proc.wait()
        await drain

    signal_group(signal.SIGTERM)
    finished = asyncio.create_task(finish())
    try:
        try:
            await asyncio.wait_for(asyncio.shield(finished), SERVER_TERMINATE_TIMEOUT)
        except TimeoutError:
            logger.warning("tlon dream pid=%s: shutdown pending; killing process group", proc.pid)
            signal_group(signal.SIGKILL)
            try:
                await asyncio.wait_for(asyncio.shield(finished), SERVER_KILL_TIMEOUT)
            except TimeoutError as exc:
                raise TimeoutError(
                    f"tlon dream process group {proc.pid} did not close after SIGKILL; "
                    f"returncode={proc.returncode}, output_done={drain.done()}:\n{server_output()}"
                ) from exc
    finally:
        # Also run on cancellation, so an interrupted test cannot leave dream alive.
        signal_group(signal.SIGKILL)
        finished.cancel()
        drain.cancel()
        await asyncio.gather(finished, drain, return_exceptions=True)


@asynccontextmanager
async def _run_server(
    extra_env: dict[str, str] | None = None,
) -> AsyncGenerator[tuple[str, Path]]:
    """Boot a real ``tlon dream`` subprocess with an isolated TLON_HOME.

    Yields (base_url, tlon_home). ``extra_env`` is merged into the
    subprocess environment on top of TLON_HOME.
    The subprocess output is drained continuously so a startup failure
    can report what the server said.
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
            env={**os.environ, "TLON_HOME": tlon_home, **(extra_env or {})},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )

        async def _drain() -> None:
            assert proc.stdout is not None
            async for chunk in proc.stdout:
                output.append(chunk.decode(errors="replace"))

        drain = asyncio.create_task(_drain())
        phase = "health probe"
        try:
            base_url = f"http://127.0.0.1:{port}"
            async with asyncio.timeout(SERVER_STARTUP_TIMEOUT):
                logger.info("tlon dream pid=%s: waiting for health at %s", proc.pid, base_url)
                await _wait_ready(base_url, proc, _server_output)
                phase = "identity initialization"
                await _initialize_identity(base_url)
            phase = "test body"
            yield base_url, Path(tlon_home)
        except BaseException:
            logger.error(
                "tlon dream pid=%s failed during %s (returncode=%s):\n%s",
                proc.pid,
                phase,
                proc.returncode,
                _server_output(),
            )
            raise
        finally:
            logger.info("tlon dream pid=%s: stopping process group", proc.pid)
            await _stop_server_process(proc, drain, _server_output)
            logger.info("tlon dream pid=%s: process group stopped and output drained", proc.pid)


@pytest.fixture
async def tlon_server() -> AsyncGenerator[tuple[str, Path]]:
    """Boot a real ``tlon dream`` subprocess with an isolated TLON_HOME.

    Yields (base_url, tlon_home). Function scope — isolation over speed.
    """
    async with _run_server() as server:
        yield server


async def _wait_ready_http(base_url: str, start_task: asyncio.Task[None]) -> None:
    """Poll GET /health until the in-process REST API answers 200.

    ``ServerApp.start()`` returns only after the port is bound, but the
    probe stays (milliseconds in-process): a bound socket is not proof
    the app serves. A dead start task fails fast instead of timing out.
    """
    deadline = asyncio.get_running_loop().time() + SERVER_STARTUP_TIMEOUT
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2.0)) as session:
        while True:
            if start_task.done():
                raise RuntimeError(
                    f"ServerApp.start() exited during startup: {start_task.exception()!r}"
                )
            try:
                async with session.get(f"{base_url}/health") as resp:
                    if resp.status == 200:
                        return
            except (aiohttp.ClientError, TimeoutError):
                pass  # Server not listening yet
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError(
                    f"in-process ServerApp not ready within {SERVER_STARTUP_TIMEOUT}s"
                )
            await asyncio.sleep(0.05)


@asynccontextmanager
async def _run_inprocess(
    extra_env: dict[str, str] | None = None,
) -> AsyncGenerator[tuple[str, Path]]:
    """Boot ``ServerApp`` in-process with an isolated TLON_HOME.

    Yields (base_url, tlon_home) — the same shape as ``_run_server``, so
    tests assert against real HTTP over 127.0.0.1 on an ephemeral port,
    unchanged. ``extra_env`` is applied to the
    process environment and the lazy ``tlon.base.settings`` singleton is
    reset so the server re-resolves settings from it; both are restored
    on teardown. Shutdown is timeout-guarded so a stuck runtime fails the
    test instead of hanging the suite.
    """
    port = _free_port()
    with (
        tempfile.TemporaryDirectory(prefix="tlon-inproc-") as tlon_home,
        pytest.MonkeyPatch.context() as mp,
    ):
        for key, value in (extra_env or {}).items():
            mp.setenv(key, value)
        app = ServerApp(
            host="127.0.0.1",
            rest_port=port,
            tlon_paths=TlonPaths(home=Path(tlon_home)),
            settings=Settings.from_env(),
        )
        await app.initialize()
        # start() blocks on the shutdown event after binding, so it
        # runs as a task; shutdown() unblocks it.
        start_task = asyncio.create_task(app.start())
        base_url = f"http://127.0.0.1:{port}"
        try:
            await _wait_ready_http(base_url, start_task)
            await _initialize_identity(base_url)
            yield base_url, Path(tlon_home)
        finally:
            try:
                await asyncio.wait_for(app.shutdown(), timeout=SERVER_SHUTDOWN_TIMEOUT)
            finally:
                if not start_task.done():
                    start_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await start_task


@pytest.fixture
async def inproc_server() -> AsyncGenerator[tuple[str, Path]]:
    """Boot ``ServerApp`` in-process with an isolated TLON_HOME.

    Yields (base_url, tlon_home). Function scope — isolation over speed.
    """
    async with _run_inprocess() as server:
        yield server


async def _assert_delivered(tlon_home: Path, member_id: str, token: str) -> None:
    """Check durable inbox immediately; no event delivery polling is necessary."""
    assert any(token in mail["content"] for mail in _read_inbox(tlon_home, member_id))


def _read_inbox(tlon_home: Path, member_id: str) -> list[dict[str, Any]]:
    """Read the post-office database through a new connection."""
    store = MailStore(TlonPaths(home=tlon_home).mail_database_file())
    return [mail.to_dict() for mail in store.mails(member_id)]


def _find_inbox_mail(tlon_home: Path, member_id: str, token: str) -> dict[str, Any]:
    """Return the inbox mail whose content carries the token."""
    for mail in _read_inbox(tlon_home, member_id):
        if token in mail.get("content", ""):
            return mail
    raise AssertionError(f"no mail with token {token!r} in {member_id}'s inbox")


WEBUI_HEADERS = {"X-Tlon-WebUI": "1"}


async def _initialize_identity(base_url: str) -> None:
    """Use the same instance setup as WebUI, then register its mailbox."""
    async with aiohttp.ClientSession(
        headers=WEBUI_HEADERS, timeout=aiohttp.ClientTimeout(total=10.0)
    ) as http:
        async with http.get(f"{base_url}/identity") as response:
            assert response.status == 200
            identity = await response.json()
        if identity["username"] is None:
            async with http.post(f"{base_url}/identity", json={"username": "alice"}) as response:
                assert response.status == 201
        async with http.get(f"{base_url}/inbox") as response:
            assert response.status == 200


async def _send_mail(
    http: aiohttp.ClientSession,
    base_url: str,
    content: str,
    *,
    to: list[str],
    cc: list[str] | None = None,
    subject: str = "",
    reply_to: str | None = None,
    thread_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Submit the WebUI wire format and assert durable acceptance."""
    from uuid import uuid4

    body: dict[str, Any] = {
        "content": content,
        "to": to,
        "subject": subject,
        "request_id": request_id or uuid4().hex,
    }
    if cc is not None:
        body["cc"] = cc
    if reply_to is not None:
        body["in_reply_to"] = reply_to
    if thread_id is not None:
        body["thread_id"] = thread_id
    async with http.post(f"{base_url}/messages", json=body) as response:
        receipt = await response.json()
        assert response.status == 202, receipt
        return dict(receipt)


def _register_human(home: Path, username: str) -> None:
    """Seed a historical correspondent without a self-reported HTTP identity."""
    MailStore(TlonPaths(home=home).mail_database_file()).register_member(f"human:{username}")
