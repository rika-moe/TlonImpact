"""Descriptor-relative access to one explicitly bound member workspace."""

from __future__ import annotations

import io
import os
import secrets
import stat
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path


class WorkspaceFiles:
    """Never follow links or reopen a checked path through the host namespace."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @contextmanager
    def _directory(self, parts: list[str], *, create: bool = False) -> Iterator[int]:
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts:
                if create:
                    with suppress(FileExistsError):
                        os.mkdir(part, dir_fd=descriptor)
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def _regular(descriptor: int) -> os.stat_result:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Workspace access requires a regular file without hard links")
        return info

    def read(self, relative: str) -> bytes:
        parts = relative.split("/")
        with self._directory(parts[:-1]) as parent:
            descriptor = os.open(
                parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
            with os.fdopen(descriptor, "rb") as stream:
                self._regular(stream.fileno())
                return stream.read()

    def write(self, relative: str, data: bytes) -> bool:
        parts = relative.split("/")
        with self._directory(parts[:-1], create=True) as parent:
            try:
                descriptor = os.open(
                    parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
                )
            except FileNotFoundError:
                existed = False
            except OSError as exc:
                raise ValueError(f"Unsafe workspace target: {exc}") from exc
            else:
                try:
                    self._regular(descriptor)
                finally:
                    os.close(descriptor)
                existed = True
            temporary = f".tlon-write-{secrets.token_hex(16)}"
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Replace the directory entry, never truncate a potentially
                # substituted inode or follow a target link after checking it.
                os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(temporary, dir_fd=parent)
            return existed

    def archive(self, relative: str, root_name: str) -> bytes:
        buffer = io.BytesIO()
        parts = relative.split("/") if relative else []
        with (
            self._directory(parts[:-1]) as parent,
            tarfile.open(fileobj=buffer, mode="w") as archive,
        ):
            self._archive_entry(
                archive, parent, parts[-1] if parts else ".", parts[-1] if parts else root_name
            )
        return buffer.getvalue()

    def _archive_entry(
        self, archive: tarfile.TarFile, parent: int, name: str, archive_name: str
    ) -> None:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(descriptor)
            entry = tarfile.TarInfo(archive_name)
            entry.mtime = info.st_mtime
            if stat.S_ISDIR(info.st_mode):
                entry.type = tarfile.DIRTYPE
                archive.addfile(entry)
                for child in sorted(os.listdir(descriptor)):
                    self._archive_entry(archive, descriptor, child, f"{archive_name}/{child}")
            else:
                self._regular(descriptor)
                with os.fdopen(os.dup(descriptor), "rb") as stream:
                    data = stream.read()
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        finally:
            os.close(descriptor)
