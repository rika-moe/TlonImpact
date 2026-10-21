"""Publish a complete staging package with a single directory rename."""

import fcntl
import os
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory


def publish_directory(target: Path, files: Mapping[str, str | bytes]) -> None:
    """Keep incomplete packages outside the catalog and remove them on failure."""
    target.parent.mkdir(parents=True, exist_ok=True)
    # A sibling of the catalog is on the same filesystem but never enumerated
    # as a staged proposal, including after all payload files have been written.
    with TemporaryDirectory(prefix=".proposal-", dir=target.parent.parent) as temporary:
        package = Path(temporary) / "package"
        package.mkdir()
        for name, content in files.items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe package path: {name!r}")
            (package / relative).parent.mkdir(parents=True, exist_ok=True)
            with (package / name).open("wb") as stream:
                stream.write(content.encode("utf-8") if isinstance(content, str) else content)
                stream.flush()
                os.fsync(stream.fileno())
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            # Serialize the short check+publication window across processes.
            # The directory itself is the lock; no stale lockfiles survive a crash.
            fcntl.flock(directory_fd, fcntl.LOCK_EX)
            if target.exists():
                raise FileExistsError(f"staging package '{target.name}' already exists")
            package.rename(target)
        finally:
            os.close(directory_fd)
