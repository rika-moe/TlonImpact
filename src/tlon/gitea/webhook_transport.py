"""Owned Gitea callback transport and persistent host-only signing secret."""

import ipaddress
import json
import os
import platform
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import URLError

from tlon.base.paths import TlonPaths
from tlon.gitea.control import StartupControl
from tlon.gitea.initialize import health_ready
from tlon.gitea.storage import GiteaError, write_private


def callback_address(client: Any, container: Any) -> str:
    """Resolve the host from the container, or use native Linux's bridge gateway."""
    system = str(client.info().get("OperatingSystem", "")).lower()
    if platform.system() in {"Darwin", "Windows"} or "docker desktop" in system:
        result = container.exec_run(
            ["/usr/bin/timeout", "-s", "KILL", "5", "getent", "hosts", "host.docker.internal"]
        )
        if result.exit_code != 0:
            raise GiteaError("Gitea cannot resolve the Docker host callback address")
        candidates = result.output.decode().splitlines()
        addresses = [line.split()[0] for line in candidates if line.split()]
    else:
        networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
        addresses = [network.get("Gateway", "") for network in networks.values()]
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError:
            continue
        if parsed.version == 4 and not (parsed.is_unspecified or parsed.is_multicast):
            return str(parsed)
    raise GiteaError("Gitea has no supported Docker host callback address")


def _secret(path: Path) -> str:
    try:
        if path.exists():
            value = json.loads(path.read_text())
            if set(value) != {"secret"} or not isinstance(value["secret"], str):
                raise ValueError
            secret = value["secret"]
            if not re.fullmatch(r"[0-9a-f]{64}", secret):
                raise ValueError
            path.chmod(0o600)
            return secret
        secret = secrets.token_hex(32)
        write_private(path, {"secret": secret})
        return secret
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise GiteaError("Invalid Gitea webhook secret; explicit recovery required") from exc


def _configure(path: Path, allowed: str, pending_restart: Path | None = None) -> bool:
    """Edit just one INI setting, retaining Gitea's unnamed global section."""
    text = path.read_text()
    sections = list(re.finditer(r"(?m)^\s*\[([^]\n]+)\]\s*$", text))
    security = [i for i, match in enumerate(sections) if match[1].lower() == "security"]
    if len(security) != 1:
        raise GiteaError("Invalid Gitea security configuration")
    index = security[0]
    start = sections[index].end()
    end = sections[index + 1].start() if index + 1 < len(sections) else len(text)
    body = text[start:end]
    matches = list(re.finditer(r"(?mi)^\s*ALLOWED_HOST_LIST\s*=([^\n]*)$", body))
    if len(matches) > 1:
        raise GiteaError("Duplicate Gitea webhook host configuration")
    line = f"ALLOWED_HOST_LIST = {allowed}"
    if matches:
        if matches[0][1].strip() == allowed:
            return False
        body = body[: matches[0].start()] + "\n" + line + body[matches[0].end() :]
    else:
        body = "\n" + line + "\n" + body
    stat = path.stat()
    fd, temporary = tempfile.mkstemp(prefix=".tlon-webhook-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text[:start] + body + text[end:])
            stream.flush()
            os.fsync(stream.fileno())
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), stat.st_mode & 0o777)
            else:
                os.chmod(temporary, stat.st_mode & 0o777)
            if hasattr(os, "fchown") and os.geteuid() == 0:
                os.fchown(stream.fileno(), stat.st_uid, stat.st_gid)
        # Persist intent before changing the file: a crash or failed restart must
        # not make the next startup mistake disk configuration for running state.
        if pending_restart is not None:
            write_private(pending_restart, {"pending": "restart"})
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def prepare_transport(
    client: Any,
    container: Any,
    paths: TlonPaths,
    instance_id: str,
    port: int,
    service_url: str,
    control: StartupControl,
) -> dict[str, str]:
    """Called only after ownership validation, under the instance lock."""
    if not 1 <= port <= 65535:
        raise GiteaError("Invalid Gitea webhook listener port")
    control.check()
    environment = dict(
        value.split("=", 1)
        for value in container.attrs.get("Config", {}).get("Env", [])
        if "=" in value
    )
    if any(
        key.upper()
        in {
            "GITEA__SECURITY__ALLOWED_HOST_LIST",
            "GITEA__WEBHOOK__ALLOWED_HOST_LIST",
        }
        for key in environment
    ):
        raise GiteaError("Gitea webhook host configuration has an unmanaged environment override")
    address = callback_address(client, container)
    secret = _secret(paths.gitea_dir() / "webhook.json")
    control.check()
    pending_restart = paths.gitea_dir() / "webhook-restart.json"
    _configure(paths.gitea_config_dir() / "app.ini", f"{address}:{port}", pending_restart)
    if pending_restart.exists():
        control.check()
        container.restart(timeout=5)
    while True:
        control.check()
        try:
            if health_ready(service_url):
                break
        except (OSError, URLError, ValueError):
            pass
        control.pause(0.2)
    pending_restart.unlink(missing_ok=True)
    return {
        "instance_id": instance_id,
        "secret": secret,
        "callback_url": f"http://{address}:{port}/gitea/{instance_id}",
    }


def probe_transport(container: Any, transport: dict[str, str], control: StartupControl) -> None:
    """Require an authenticated round trip from the owned container before ready."""
    import hashlib
    import hmac
    import uuid

    body = json.dumps({"tlon_probe": transport["instance_id"]}, separators=(",", ":"))
    signature = hmac.new(transport["secret"].encode(), body.encode(), hashlib.sha256).hexdigest()
    control.check()
    result = container.exec_run(
        [
            "/usr/bin/timeout",
            "-s",
            "KILL",
            "10",
            "wget",
            "-T",
            "5",
            "-qO-",
            "--header",
            "Content-Type: application/json",
            "--header",
            "X-Gitea-Event-Type: ping",
            "--header",
            f"X-Gitea-Delivery: {uuid.uuid4()}",
            "--header",
            f"X-Gitea-Signature: {signature}",
            "--post-data",
            body,
            transport["callback_url"],
        ]
    )
    control.check()
    try:
        valid = result.exit_code == 0 and json.loads(result.output) == {"probe": True}
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise GiteaError("Gitea webhook callback verification failed")
