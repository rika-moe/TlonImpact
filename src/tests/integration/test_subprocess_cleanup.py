"""Pin process-tree cleanup when the launcher exits before its server."""

import asyncio
import contextlib
import os
import signal
import socket
import sys

import pytest

from . import conftest


@pytest.mark.parametrize("ignore_term", [False, True])
async def test_cleanup_reaps_descendant_holding_stdout(
    monkeypatch: pytest.MonkeyPatch, ignore_term: bool
) -> None:
    monkeypatch.setattr(conftest, "SERVER_TERMINATE_TIMEOUT", 0.15)
    monkeypatch.setattr(conftest, "SERVER_KILL_TIMEOUT", 3.0)
    child = (
        "import os,signal,socket,time; "
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN); " if ignore_term else "")
        + "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(); "
        "print(os.getpid(), s.getsockname()[1], flush=True); time.sleep(60)"
    )
    wrapper = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)"
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        wrapper,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    assert proc.stdout is not None
    drain: asyncio.Task[None] | None = None
    try:
        line = await asyncio.wait_for(proc.stdout.readline(), 5.0)
        child_pid, port = map(int, line.split())

        async def consume() -> None:
            assert proc.stdout is not None
            await proc.stdout.read()

        drain = asyncio.create_task(consume())
        # Reproduce the old teardown: terminate only the launcher. Its child
        # keeps both the listening socket and inherited output pipe open.
        proc.terminate()
        async with asyncio.timeout(3.0):
            while proc.returncode is None:
                await asyncio.sleep(0.01)
        assert not drain.done()
        os.kill(child_pid, 0)
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            pass

        await asyncio.wait_for(
            conftest._stop_server_process(proc, drain, lambda: line.decode()), 5.0
        )
        assert drain.done() and not drain.cancelled()
        with socket.socket() as probe:
            probe.settimeout(0.2)
            assert probe.connect_ex(("127.0.0.1", port)) != 0
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        if drain is not None:
            drain.cancel()
            await asyncio.gather(drain, return_exceptions=True)
        await asyncio.wait_for(proc.wait(), 3.0)


async def test_real_cli_cleanup_releases_listener() -> None:
    from urllib.parse import urlsplit

    async with conftest._run_server() as (url, _):
        port = urlsplit(url).port
        assert port is not None
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            pass
    with socket.socket() as probe:
        probe.settimeout(0.2)
        assert probe.connect_ex(("127.0.0.1", port)) != 0


async def test_cli_startup_deadline_cleans_group_and_reports_phase(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from collections.abc import Callable

    started: list[asyncio.subprocess.Process] = []

    async def stalled_health(
        url: str, proc: asyncio.subprocess.Process, output: Callable[[], str]
    ) -> None:
        started.append(proc)
        await asyncio.Event().wait()

    monkeypatch.setattr(conftest, "SERVER_STARTUP_TIMEOUT", 0.1)
    monkeypatch.setattr(conftest, "_wait_ready", stalled_health)
    with pytest.raises(TimeoutError):
        async with conftest._run_server():
            pytest.fail("unready server was yielded")
    assert len(started) == 1 and started[0].returncode is not None
    assert "failed during health probe" in caplog.text
    assert "returncode=" in caplog.text
