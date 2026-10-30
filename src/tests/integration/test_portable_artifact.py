"""Run the shipped frozen EXE, never a Python approximation of its entry point."""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from zipfile import ZipFile

import pytest

from tlon.base.paths import TlonPaths
from tlon.organization.mail.store import MailStore

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or "TLON_PORTABLE_ZIP" not in os.environ,
    reason="Run scripts/check_all.sh --windows-portable against a built Windows ZIP",
)


def _request(url: str, body: dict[str, Any] | None = None) -> Any:
    request = Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Tlon-WebUI": "1", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        content = response.read()
        if "json" in response.headers.get("Content-Type", ""):
            return json.loads(content)
        return content


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    return {
        str(item.relative_to(root)): (item.stat().st_size, item.stat().st_mtime_ns)
        for item in root.rglob("*")
        if item.is_file()
    }


def _ready(process: subprocess.Popen[bytes], data: Path) -> str:
    from tlon_launcher.portable_instance import request_existing

    deadline = time.monotonic() + 120
    last = "not started"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            logs = [
                str(path) + "\n" + path.read_text(encoding="utf-8", errors="replace")[-12000:]
                for path in data.rglob("*.log")
            ]
            raise AssertionError(f"Frozen EXE exited {process.returncode}: {logs}")
        try:
            status = request_existing(data, action="status", timeout=1.0)
            if status and status.get("status") == "ready":
                return str(status["url"]).removesuffix("/ui/")
            last = repr(status)
        except (OSError, ValueError, RuntimeError) as exc:
            last = str(exc)
        time.sleep(0.2)
    raise AssertionError(f"Frozen EXE never became ready: {last}")


def _stop(executable: Path, process: subprocess.Popen[bytes], cwd: Path, port: int) -> None:
    stopped = subprocess.run([str(executable), "--quit"], cwd=cwd, timeout=30, check=False)
    assert stopped.returncode == 0
    assert process.wait(timeout=40) == 0
    with socket.socket() as client:
        assert client.connect_ex(("127.0.0.1", port)) != 0


@pytest.fixture
def mock_model() -> Iterator[tuple[str, list[dict[str, Any]]]]:
    calls: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            calls.append(request)
            message: dict[str, Any] = {"role": "assistant", "content": "ready"}
            finish = "stop"
            if request.get("tools"):
                turn = sum(bool(call.get("tools")) for call in calls)
                name, arguments = [
                    (
                        "file-write",
                        {"file_path": "portable-proof.txt", "content": "frozen workspace proof"},
                    ),
                    (
                        "mail-send",
                        {"to": ["portable-user@tlon.internal"], "content": "frozen-member-reply"},
                    ),
                    ("finish-turn", {}),
                ][(turn - 1) % 3]
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"portable-tool-{turn}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
                finish = "tool_calls"
            payload = json.dumps(
                {
                    "id": "portable-smoke",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": finish,
                            "message": message,
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_shipped_executable_mail_and_relocation(
    tmp_path: Path, mock_model: tuple[str, list[dict[str, Any]]]
) -> None:
    release = Path(os.environ["TLON_PORTABLE_ZIP"])
    with ZipFile(release) as archive:
        assert not any(name.startswith("Tlon/data/") for name in archive.namelist())
        archive.extractall(tmp_path / "中文 空格")
    folder = tmp_path / "中文 空格/Tlon"
    executable = folder / "Tlon.exe"
    assert executable.is_file() and (folder / "runtime/python313.dll").is_file()
    sources = list((folder / "runtime/tlon").rglob("*.py"))
    assert sources and sum(path.stat().st_size for path in sources) > 100_000
    assert (folder / "runtime/tlosure").is_dir()
    assert len(list((folder / "runtime/tlon/prototypes").glob("*/behavior.tlon"))) == 7
    cwd = tmp_path / "unrelated-cwd"
    cwd.mkdir()
    outside = tmp_path / "external-tlon-home"
    outside.mkdir()
    (outside / "sentinel").write_bytes(b"untouched")
    original_home = _snapshot(Path.home() / ".tlon")
    runtime_snapshot = _snapshot(folder / "runtime")
    env = dict(
        os.environ,
        TLON_HOME=str(outside),
        OPENAI_API_BASE=mock_model[0],
        DEEPSEEK_API_KEY="",
        DOCKER_HOST="tcp://127.0.0.1:1",
    )
    # A clean EXE must not resolve Python/uv/Node/Docker from developer PATH.
    env["PATH"] = str(Path(os.environ["SYSTEMROOT"]) / "System32")
    token = "frozen-" + uuid.uuid4().hex
    member = ""
    for iteration in range(2):
        executable = folder / "Tlon.exe"
        data = folder / "data"
        process = subprocess.Popen([str(executable), "--headless"], cwd=cwd, env=env)
        try:
            base = _ready(process, data)
            port = int(base.rsplit(":", 1)[1])
            assert b"<html" in _request(base + "/ui/").lower()
            assert _request(base + "/health")
            duplicate = subprocess.run(
                [str(executable), "--headless"], cwd=cwd, env=env, timeout=30, check=False
            )
            assert duplicate.returncode == 0
            assert process.poll() is None
            assert _ready(process, data) == base
            identity = _request(base + "/identity")
            if iteration == 0:
                assert identity["username"] is None
                _request(base + "/identity", {"username": "portable-user"})
                # First-use configuration must update already-booted clients,
                # not merely win a race against their initial assembly.
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    agents = _request(base + "/agents")["agents"]
                    if len(agents) == 30 and all(agent["resident"] for agent in agents):
                        break
                    time.sleep(0.2)
                else:
                    raise AssertionError("Initial society did not finish resident boot")
                member = _request(base + "/admin/llm/setup")["initial_member_ids"][0]
                setup = _request(
                    base + "/admin/llm/setup",
                    {
                        "provider": "openai",
                        "model": "openai/gpt-4o-mini",
                        "api_key": "portable-test-key",
                        "scope": "initial",
                        "agent_ids": [],
                    },
                )
                assert setup["required"] is False
                assert mock_model[1], "Frozen LiteLLM provider must reach the local model"
                assert (data / ".env").is_file()
                _request(base + "/inbox")
                _request(
                    base + "/messages",
                    {
                        "to": ["portable-user"],
                        "content": token,
                        "subject": "Frozen delivery",
                        "request_id": uuid.uuid4().hex,
                    },
                )
            else:
                assert identity["username"] == "portable-user"
            store = MailStore(TlonPaths(home=data).mail_database_file())
            assert any(token in mail.content for mail in store.mails("human:portable-user"))
            if iteration == 0:
                _request(
                    base + "/messages",
                    {
                        "to": [member],
                        "content": "Write a workspace note and reply.",
                        "subject": "Frozen member execution",
                        "request_id": uuid.uuid4().hex,
                    },
                )
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    replies = [
                        mail
                        for mail in store.mails("human:portable-user")
                        if mail.content == "frozen-member-reply"
                        and mail.from_address == f"{member}@tlon.internal"
                    ]
                    database = data / member / "program.sqlite3"
                    completed = 0
                    if database.exists():
                        with sqlite3.connect(database) as connection:
                            completed = connection.execute(
                                "SELECT COUNT(*) FROM runs WHERE completed=1 "
                                "AND run_id LIKE '%:handler:mail-delivered'"
                            ).fetchone()[0]
                    if replies and completed:
                        break
                    time.sleep(0.2)
                else:
                    raise AssertionError(
                        "Frozen member did not complete its tool/reply/finish loop"
                    )
                tool_requests = [call for call in mock_model[1] if call.get("tools")]
                assert len(tool_requests) >= 3
                tools = {tool["function"]["name"] for tool in tool_requests[0]["tools"]}
                assert {"file-write", "file-read", "file-edit", "file-list", "file-search"} <= tools
                assert not {"shell-run", "shell-check"} & tools
            assert (
                data / member / "workspace/portable-proof.txt"
            ).read_text() == "frozen workspace proof"
            assert any(
                mail.content == "frozen-member-reply" for mail in store.mails("human:portable-user")
            )
            _stop(executable, process, cwd, port)
        finally:
            if process.poll() is None:
                subprocess.run([str(executable), "--quit"], cwd=cwd, timeout=15, check=False)
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
        assert _snapshot(outside) == {"sentinel": (9, (outside / "sentinel").stat().st_mtime_ns)}
        assert not list(cwd.iterdir())
        assert _snapshot(Path.home() / ".tlon") == original_home
        assert _snapshot(folder / "runtime") == runtime_snapshot
        for log in data.rglob("*.log"):
            assert "portable-test-key" not in log.read_text(encoding="utf-8", errors="replace")
        if iteration == 0:
            moved = tmp_path / "移动后的 文件夹"
            shutil.move(str(folder), moved)
            folder = moved

    # Invalid data layout must fail promptly, without any orphan service.
    preserved = folder / "saved-data"
    (folder / "data").rename(preserved)
    (folder / "data").write_bytes(b"not a directory")
    try:
        failed = subprocess.run(
            [str(folder / "Tlon.exe"), "--headless"], cwd=cwd, env=env, timeout=30, check=False
        )
        assert failed.returncode != 0
        with socket.socket() as client:
            assert client.connect_ex(("127.0.0.1", port)) != 0
    finally:
        (folder / "data").unlink()
        preserved.rename(folder / "data")
