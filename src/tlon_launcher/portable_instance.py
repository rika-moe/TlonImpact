"""Single-data-directory ownership and authenticated loopback control."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from filelock import FileLock


def signature(secret: str, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(bytes.fromhex(secret), body, hashlib.sha256).hexdigest()


class InstanceLease:
    """The OS releases the lock after crashes; only its owner replaces stale state."""

    def __init__(self, control: Path) -> None:
        self.state_path = control / "instance.json"
        self.lock = FileLock(str(control / "instance.lock"), timeout=0)
        self.secret = secrets.token_hex(32)

    def acquire(self) -> None:
        self.lock.acquire()

    def publish(self, port: int) -> None:
        state = {"version": 1, "pid": os.getpid(), "port": port, "secret": self.secret}
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        os.replace(temporary, self.state_path)

    def close(self) -> None:
        if self.lock.is_locked:
            try:
                self.state_path.unlink(missing_ok=True)
            finally:
                self.lock.release()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def request_existing(data: Path, action: str = "status", *, timeout: float = 15) -> dict[str, Any]:
    """Never send a secret or accept an unauthenticated listener as our instance."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    while time.monotonic() < deadline:
        try:
            state = json.loads((data / ".portable" / "instance.json").read_text(encoding="utf-8"))
            port, secret = state["port"], state["secret"]
            if state["version"] != 1 or not isinstance(port, int) or not 1 <= port <= 65535:
                raise ValueError("无效实例控制状态")
            nonce = secrets.token_hex(32)
            payload = {"nonce": nonce, "action": action}
            body = json.dumps({"payload": payload, "mac": signature(secret, payload)}).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/control",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with opener.open(
                request, timeout=min(2, max(0.1, deadline - time.monotonic()))
            ) as response:
                raw = response.read(4097)
                if len(raw) > 4096:
                    raise ValueError("无效实例控制响应")
                reply = json.loads(raw)
            result = reply["payload"]
            if result.get("nonce") != nonce or not hmac.compare_digest(
                reply["mac"], signature(secret, result)
            ):
                raise ValueError("本机端口未通过 Tlon 实例身份验证")
            if action == "open" and result["status"] == "starting":
                time.sleep(0.1)
                continue
            return dict(result)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError("无法联系此文件夹的 Tlon 实例；请稍后重试。") from last_error
