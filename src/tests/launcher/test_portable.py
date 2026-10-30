"""Portable path ownership, authenticated duplicate launch, and real control lifecycle."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from filelock import Timeout

from tlon_launcher import portable_paths
from tlon_launcher.portable_instance import InstanceLease, request_existing
from tlon_launcher.portable_paths import PortableLayout, configure_environment, prepare_layout
from tlon_launcher.portable_runtime import PortableRuntime


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PortableLayout:
    monkeypatch.setattr(portable_paths, "validate_drive", lambda root: None)
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    executable = tmp_path / "中文 空格 Tlon" / "Tlon.exe"
    executable.parent.mkdir()
    executable.touch()
    result = prepare_layout(executable)
    configure_environment(result)
    portable_paths.seed_tokenizer_cache(result)
    return result


def test_paths_ignore_cwd_home_and_external_tlon_home(
    layout: PortableLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    monkeypatch.setenv("TLON_HOME", str(other / "foreign"))
    monkeypatch.setenv("HOME", str(other))
    rebuilt = prepare_layout(layout.root / "Tlon.exe")
    assert rebuilt.data == layout.root / "data"
    configure_environment(rebuilt)
    assert os.environ["TLON_HOME"] == str(rebuilt.data)
    assert os.environ["HOME"] == str(other)
    assert tempfile.gettempdir() == str(rebuilt.data / "tmp")
    assert os.environ["CUSTOM_TIKTOKEN_CACHE_DIR"] == str(rebuilt.data / "cache" / "tiktoken")
    assert list(other.iterdir()) == []


def test_linked_data_is_not_followed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(portable_paths, "validate_drive", lambda root: None)
    root, outside = tmp_path / "program", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "data").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="链接"):
        prepare_layout(root / "Tlon.exe")
    assert list(outside.iterdir()) == []


def test_unwritable_data_never_falls_back(
    layout: PortableLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny(*args: Any, **kwargs: Any) -> None:
        raise PermissionError("read only")

    monkeypatch.setattr(tempfile, "TemporaryFile", deny)
    with pytest.raises(PermissionError):
        prepare_layout(layout.root / "Tlon.exe")


def test_instance_lock_duplicate_and_stale_state(layout: PortableLayout) -> None:
    first, second = InstanceLease(layout.control), InstanceLease(layout.control)
    first.acquire()
    try:
        first.publish(12345)
        with pytest.raises(Timeout):
            second.acquire()
    finally:
        first.close()
    assert not first.state_path.exists()
    first.state_path.write_text('{"stale": true}', encoding="utf-8")
    second.acquire()
    try:
        second.publish(23456)
        assert json.loads(first.state_path.read_text())["port"] == 23456
    finally:
        second.close()


def test_unknown_listener_is_not_an_existing_instance(layout: PortableLayout) -> None:
    received: list[bytes] = []

    class Impostor(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            data = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(data)
            payload = json.loads(data)["payload"]
            reply = {
                "payload": {"nonce": payload["nonce"], "status": "ready", "url": "http://evil/"},
                "mac": "wrong",
            }
            body = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Impostor)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    lease = InstanceLease(layout.control)
    lease.acquire()
    try:
        lease.publish(server.server_port)
        with pytest.raises(RuntimeError, match="无法联系"):
            request_existing(layout.data, "open", timeout=0.3)
        assert received and all(lease.secret.encode() not in body for body in received)
    finally:
        lease.close()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("failure", [None, "initialize", "shutdown"])
def test_control_ready_duplicate_and_shutdown_errors(
    layout: PortableLayout, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    from aiohttp import web

    from tlon_server import serverapp
    from tlon_server.api import rest

    assets = layout.root / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("ready", encoding="utf-8")
    monkeypatch.setattr(rest, "WEBUI_DIST_DIR", assets)
    monkeypatch.setattr("tlon.base.logger.configure_logging", lambda **kwargs: None)
    instances: list[Any] = []

    class App:
        def __init__(self, **kwargs: Any) -> None:
            assert kwargs["tlon_paths"].env_file() == layout.data / ".env"
            self.ready = asyncio.Event()
            self.rest_port = 0
            self.runner: web.AppRunner | None = None
            self.rest = type("Rest", (), {"stop": AsyncMock(side_effect=self.close)})()
            self.shutdown_count = 0
            instances.append(self)

        async def initialize(self) -> None:
            if failure == "initialize":
                raise RuntimeError("injected startup failure")

        async def start(self) -> None:
            app = web.Application()

            async def index(request: web.Request) -> web.Response:
                return web.Response(text="ready")

            app.router.add_get("/ui/", index)
            self.runner = web.AppRunner(app)
            await self.runner.setup()
            await web.TCPSite(self.runner, "127.0.0.1", 0).start()
            self.rest_port = self.runner.addresses[0][1]
            self.ready.set()
            await asyncio.Event().wait()

        async def close(self) -> None:
            if self.runner:
                await self.runner.cleanup()

        async def shutdown(self) -> None:
            self.shutdown_count += 1
            if failure == "shutdown":
                raise RuntimeError("injected shutdown failure")
            await self.close()

    monkeypatch.setattr(serverapp, "ServerApp", App)
    lease = InstanceLease(layout.control)
    lease.acquire()
    events: list[tuple[str, str]] = []
    runtime = PortableRuntime(layout, lease, lambda event, value: events.append((event, value)))
    try:
        runtime.start()
        if failure != "initialize":
            result = request_existing(layout.data, "open", timeout=10)
            assert result["status"] == "ready"
            assert result["url"].endswith("/ui/")
            # A second launcher uses authenticated state and never starts a second runtime.
            from tlon_launcher.portable import main

            assert main(["--headless"], executable=layout.root / "Tlon.exe") == 0
            assert len(instances) == 1
            request_existing(layout.data, "quit")
        assert runtime.stopped.wait(10), "portable worker did not stop"
        runtime.join()
        assert bool(runtime.error) == bool(failure)
        assert events[-1][0] == "stopped"
        assert ("error" in [event for event, _ in events]) == bool(failure)
        assert instances[0].shutdown_count == 1
        instances[0].rest.stop.assert_awaited_once()
    finally:
        runtime.stop()
        runtime.join()
        lease.close()


def test_saved_env_wins_and_cache_cannot_escape(
    layout: PortableLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tlon_launcher.portable import main

    (layout.data / ".env").write_text(
        'OPENAI_API_KEY="saved-${OTHER_KEY}"\nTLON_HOME=foreign\nTIKTOKEN_CACHE_DIR=foreign\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "external")
    monkeypatch.setenv("OTHER_KEY", "expanded")
    monkeypatch.setattr("tlon_launcher.portable_instance.request_existing", lambda *a, **kw: {})
    assert main(["--quit", "--headless"], executable=layout.root / "Tlon.exe") == 0
    assert os.environ["OPENAI_API_KEY"] == "saved-${OTHER_KEY}"
    assert os.environ["TLON_HOME"] == str(layout.data)
    assert os.environ["TIKTOKEN_CACHE_DIR"] == str(layout.data / "cache" / "tiktoken")


def test_log_permission_failure_returns_error_without_fallback(
    layout: PortableLayout, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from tlon_launcher.portable import main

    original = Path.open

    def refuse_log(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.name == "portable.log":
            raise PermissionError("日志目录只读")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_log)
    assert main(["--headless"], executable=layout.root / "Tlon.exe") == 1
    assert "日志目录只读" in capsys.readouterr().err
    assert not (layout.control / "instance.json").exists()
    lease = InstanceLease(layout.control)
    lease.acquire()
    lease.close()


def test_main_reports_worker_failure_and_releases_lock(
    layout: PortableLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tlon_launcher.portable import main

    class FailedRuntime:
        error = "injected runtime failure"

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def start(self) -> None:
            pass

        def join(self) -> None:
            pass

    monkeypatch.setattr("tlon_launcher.portable_runtime.PortableRuntime", FailedRuntime)
    assert main(["--headless"], executable=layout.root / "Tlon.exe") == 1
    lease = InstanceLease(layout.control)
    lease.acquire()
    lease.close()


@pytest.mark.skipif(os.name != "nt", reason="native Windows tray window")
@pytest.mark.parametrize("event", ["ready", "error"])
def test_native_tray_dispatch_and_cleanup(
    layout: PortableLayout, monkeypatch: pytest.MonkeyPatch, event: str
) -> None:
    from tlon_launcher import portable_tray

    opened: list[str] = []
    errors: list[str] = []
    monkeypatch.setattr(portable_tray, "open_browser", opened.append)
    monkeypatch.setattr(portable_tray, "show_error", lambda message, logs: errors.append(message))
    tray = portable_tray.Tray(layout)

    class Worker:
        joined = False

        def start(self) -> None:
            tray.notify(event, "http://127.0.0.1:43210/ui/" if event == "ready" else "failure")
            tray.notify("stopped", "")

        def stop(self) -> None:
            if portable_tray.gui.IsWindow(tray.hwnd):
                tray.notify("stopped", "")

        def join(self) -> None:
            self.joined = True

    worker = Worker()
    watchdog = threading.Timer(5, worker.stop)
    watchdog.start()
    try:
        tray.run(worker)  # type: ignore[arg-type] -- a deterministic worker for the native UI boundary.
    finally:
        watchdog.cancel()
    assert worker.joined
    assert not portable_tray.gui.IsWindow(tray.hwnd)
    assert bool(opened) == (event == "ready")
    assert bool(errors) == (event == "error")
