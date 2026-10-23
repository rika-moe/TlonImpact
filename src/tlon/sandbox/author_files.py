"""Runtime author materials exposed at fixed, read-only workspace paths."""

from __future__ import annotations

import io
import posixpath
import tarfile
from pathlib import Path


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


def author_path(path: str, working_dir: str) -> tuple[str, Path] | None:
    """Resolve only published files; reject links even within the resource tree."""
    candidate = posixpath.normpath(posixpath.join(working_dir, path))
    candidate = "/" + candidate.lstrip("/")
    for target, source in author_mounts(working_dir).items():
        if candidate == target or candidate.startswith(target + "/"):
            relative = candidate.removeprefix(target).lstrip("/")
            local = source
            for component in relative.split("/") if relative else []:
                local /= component
                if local.is_symlink():
                    raise ValueError("Symbolic links are not allowed in author material paths")
            if not local.exists():
                raise FileNotFoundError(candidate)
            return candidate, local
    return None


def author_archive(path: Path) -> bytes:
    """Reuse the existing archive listing/search implementation without a device."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        entries = [path, *sorted(path.rglob("*"))] if path.is_dir() else [path]
        for entry in entries:
            if entry.is_symlink():
                raise ValueError("Symbolic links are not allowed in author materials")
            archive.add(entry, arcname=str(entry.relative_to(path.parent)), recursive=False)
    return buffer.getvalue()
