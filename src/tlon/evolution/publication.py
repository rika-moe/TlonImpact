"""Publish a complete staging package with a single directory rename."""

import os
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory

from tlon.base.directory_lock import directory_lock


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
        with directory_lock(target.parent):
            # Serialize the short check+publication window across processes.
            if target.exists():
                raise FileExistsError(f"staging package '{target.name}' already exists")
            package.rename(target)
