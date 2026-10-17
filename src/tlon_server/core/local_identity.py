"""Persistent identity for the single local WebUI operator (ADR 0033)."""

import json
import os
import re
import tempfile
from pathlib import Path

_USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", re.ASCII)
_RESERVED = frozenset({"postmaster", "adam"})


def validate_username(username: object) -> str:
    """Validate an address local part without silently changing its identity."""
    if not isinstance(username, str) or _USERNAME.fullmatch(username) is None:
        raise ValueError(
            "Use 1–64 ASCII letters, numbers, dots, underscores or hyphens; start with a letter or number"
        )
    if username.casefold() in _RESERVED:
        raise ValueError("This username is reserved")
    return username


class LocalIdentity:
    """An explicitly injected identity file; initialization is atomic and exclusive."""

    def __init__(self, path: Path):
        self._path = path
        self._username: str | None = self._load()

    def _load(self) -> str | None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except UnicodeError as exc:
            raise RuntimeError(f"Invalid WebUI identity file: {self._path}") from exc
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or set(data) != {"username"}:
                raise ValueError("Expected an identity object with only username")
            return validate_username(data["username"])
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError(f"Invalid WebUI identity file: {self._path}") from exc

    @property
    def username(self) -> str | None:
        return self._username

    @property
    def address(self) -> str | None:
        return f"{self._username}@tlon.internal" if self._username is not None else None

    def require_username(self) -> str:
        """Return the initialized username; API middleware guards this invariant."""
        if self._username is None:
            raise RuntimeError("WebUI identity has not been initialized")
        return self._username

    def initialize(self, username: str) -> None:
        """Publish a complete file exactly once, including across server processes."""
        username = validate_username(username)
        if self._username is not None:
            raise FileExistsError("WebUI identity already initialized")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".webui-identity-", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"username": username}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, self._path)
            except FileExistsError:
                self._username = self._load()
                raise
            self._username = username
        finally:
            os.unlink(temporary)
