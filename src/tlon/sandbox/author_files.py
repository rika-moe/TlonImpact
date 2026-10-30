"""Runtime author materials exposed at fixed, read-only workspace paths."""

from __future__ import annotations

import io
import posixpath
import stat
import sys
import tarfile
from collections.abc import Iterator
from pathlib import Path

from tlon.sandbox.windows_paths import validate_windows_component


def author_root(working_dir: str) -> str:
    return "/" + posixpath.normpath(posixpath.join(working_dir, "tlon")).lstrip("/")


def author_mounts(working_dir: str) -> dict[str, Path]:
    """Use the same documentation sources in checkout and built distributions."""
    package = Path(__file__).resolve().parents[1]
    docs = package / "resources" / "docs"
    if not docs.is_dir():
        docs = package.parents[1] / "docs"
    if not docs.is_dir():
        raise RuntimeError("Runtime author documentation is missing")
    root = author_root(working_dir)
    return {f"{root}/docs": docs, f"{root}/stdlib": package / "resources" / "stdlib"}


def is_author_path(path: str, working_dir: str) -> bool:
    root = author_root(working_dir)
    return path == root or path.startswith(root + "/")


def _reject_links(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError("Symbolic links and reparse points are not allowed in author materials")


def author_path(path: str, working_dir: str) -> tuple[str, Path] | None:
    """Resolve only published files; reject links even within the resource tree."""
    candidate = posixpath.normpath(posixpath.join(working_dir, path))
    candidate = "/" + candidate.lstrip("/")
    for target, source in author_mounts(working_dir).items():
        if candidate == target or candidate.startswith(target + "/"):
            relative = candidate.removeprefix(target).lstrip("/")
            local = source
            _reject_links(local)
            for component in relative.split("/") if relative else []:
                if sys.platform == "win32":
                    validate_windows_component(component)
                local /= component
                _reject_links(local)
            if not local.exists():
                raise FileNotFoundError(candidate)
            return candidate, local
    return None


def author_archive(path: Path) -> bytes:
    """Reuse the existing archive listing/search implementation without a device."""

    def entries(current: Path) -> Iterator[Path]:
        # Check before descending: Windows junctions must never be traversed.
        _reject_links(current)
        yield current
        if current.is_dir():
            for child in sorted(current.iterdir()):
                yield from entries(child)

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for entry in entries(path):
            archive.add(entry, arcname=str(entry.relative_to(path.parent)), recursive=False)
    return buffer.getvalue()
