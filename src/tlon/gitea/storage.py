"""Atomic private bootstrap records; missing credentials never reset users."""

import json
import os
import secrets
import uuid
from pathlib import Path

from tlon.base.paths import TlonPaths


class GiteaError(Exception):
    """An intentionally credential-free diagnostic."""


def write_private(path: Path, value: dict[str, str | int]) -> None:
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    path.chmod(0o600)


def load_records(paths: TlonPaths, port: int) -> tuple[dict[str, str | int], dict[str, str]]:
    root = paths.gitea_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    state_path = paths.gitea_state_file()
    credentials_path = paths.gitea_credentials_file()
    try:
        if state_path.exists():
            state = json.loads(state_path.read_text())
            if (
                set(state) != {"instance_id", "port", "home"}
                or str(uuid.UUID(state["instance_id"])) != state["instance_id"]
                or state["port"] != port
                or state["home"] != str(root.resolve())
            ):
                raise ValueError
        else:
            if (
                credentials_path.exists()
                or paths.gitea_data_dir().exists()
                or paths.gitea_config_dir().exists()
            ):
                raise ValueError
            state = {"instance_id": str(uuid.uuid4()), "port": port, "home": str(root.resolve())}
            write_private(state_path, state)
        if credentials_path.exists():
            credentials = json.loads(credentials_path.read_text())
            if set(credentials) != {"admin_password", "operator_password"} or not all(
                isinstance(value, str) and len(value) >= 32 for value in credentials.values()
            ):
                raise ValueError
        else:
            if paths.gitea_data_dir().exists() or paths.gitea_config_dir().exists():
                raise ValueError
            credentials = {
                "admin_password": secrets.token_urlsafe(36),
                "operator_password": secrets.token_urlsafe(36),
            }
            write_private(credentials_path, credentials)
        if not paths.gitea_operator_credentials_file().exists():
            write_private(
                paths.gitea_operator_credentials_file(),
                {"username": "tlon-operator", "initial_password": credentials["operator_password"]},
            )
        credentials_path.chmod(0o600)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise GiteaError("Invalid Gitea persistent state; explicit recovery required") from exc
    return state, credentials
