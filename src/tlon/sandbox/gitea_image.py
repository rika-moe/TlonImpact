"""Bundled development image recipe and ephemeral managed Gitea environment."""

from __future__ import annotations

import hashlib
import io
import re
import tarfile
import threading
import time
from importlib.resources import files
from urllib.parse import urlsplit

import docker
from docker.errors import ImageNotFound

_BUILD_LOCK = threading.Lock()
# Cold Node base pulls plus compiler/Python packages exceed the former CLI-only budget.
GITEA_IMAGE_BUILD_TIMEOUT_SECONDS = 900

_ASSETS = ("Dockerfile", "git-credential-tlon")


def _build_assets() -> dict[str, bytes]:
    root = files("tlon.sandbox").joinpath("gitea_assets")
    return {name: root.joinpath(name).read_bytes() for name in _ASSETS}


def gitea_image_tag() -> str:
    """Recipe identity changes whenever bundled build inputs change."""
    digest = hashlib.sha256()
    for name, content in _build_assets().items():
        digest.update(name.encode() + b"\0" + content + b"\0")
    return f"tlon-gitea-exec:{digest.hexdigest()[:20]}"


def ensure_gitea_image(client: docker.DockerClient, *, stop: threading.Event | None = None) -> str:
    """Reuse a local recipe image or build it once using packaged assets."""
    started = time.monotonic()

    def check() -> None:
        if (
            stop is not None and stop.is_set()
        ) or time.monotonic() - started > GITEA_IMAGE_BUILD_TIMEOUT_SECONDS:
            raise RuntimeError("Gitea execution image build stopped or timed out")

    # Members are assembled concurrently; only one thread builds the shared recipe.
    while not _BUILD_LOCK.acquire(timeout=0.1):
        check()
    try:
        check()
        tag = gitea_image_tag()
        try:
            client.images.get(tag)
        except ImageNotFound:
            context = io.BytesIO()
            with tarfile.open(fileobj=context, mode="w") as archive:
                for name, content in _build_assets().items():
                    info = tarfile.TarInfo(name)
                    info.size = len(content)
                    info.mode = 0o755 if name == "git-credential-tlon" else 0o644
                    archive.addfile(info, io.BytesIO(content))
            context.seek(0)
            stream = client.api.build(
                fileobj=context, custom_context=True, tag=tag, rm=True, timeout=120, decode=True
            )
            try:
                for event in stream:
                    check()
                    if "error" in event:
                        raise RuntimeError("Gitea execution image build failed")
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
            check()
            client.images.get(tag)
        return tag
    finally:
        _BUILD_LOCK.release()


def execution_environment(url: str, username: str, token: str) -> dict[str, str]:
    """Build ephemeral auth for both native tea and Git's credential protocol.

    URL is the internal service origin, not Gitea's browser ROOT_URL. Tokens
    remain environment values, never command arguments or repository config.
    """
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or any(c.isspace() for c in url)
    ):
        raise ValueError("Gitea execution URL must be an HTTP(S) origin")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", username):
        raise ValueError("Invalid Gitea execution username")
    if not token or any(c.isspace() for c in token):
        raise ValueError("Invalid Gitea execution token")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return {
        "TLON_GITEA_URL": origin,
        "TLON_GITEA_USERNAME": username,
        "TLON_GITEA_TOKEN": token,
        "GITEA_INSTANCE_URL": origin,
        "GITEA_TOKEN": token,
        # tea probes/creates its config directory even with environment auth.
        # /tmp is per-execution tmpfs, never the persistent workspace.
        "XDG_CONFIG_HOME": "/tmp/tlon-config",
        "npm_config_cache": "/tmp/npm-cache",
        "PIP_CACHE_DIR": "/tmp/pip-cache",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": "/usr/local/bin/git-credential-tlon",
        "GIT_CONFIG_KEY_2": "http.followRedirects",
        "GIT_CONFIG_VALUE_2": "false",
        "GIT_AUTHOR_NAME": username,
        "GIT_AUTHOR_EMAIL": f"{username}@tlon.invalid",
        "GIT_COMMITTER_NAME": username,
        "GIT_COMMITTER_EMAIL": f"{username}@tlon.invalid",
    }
