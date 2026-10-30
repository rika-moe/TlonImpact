"""Windows workspace access relative to verified, retained directory handles."""

from __future__ import annotations

import ctypes
import io
import secrets
import sys
import tarfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pywintypes
import win32con
import win32file

from tlon.sandbox.windows_paths import validate_windows_component

if sys.platform == "win32":
    from tlon.sandbox._windows_handles import open_relative

assert sys.platform == "win32", "Windows native module"

_CHUNK = 1024 * 1024
_FILE_READ_ATTRIBUTES = 0x80
_FILE_LIST_DIRECTORY = 0x1


@contextmanager
def _os_errors() -> Iterator[None]:
    try:
        yield
    except pywintypes.error as exc:
        raise ctypes.WinError(exc.winerror) from exc


def _attributes(handle: int, *, regular: bool = False) -> tuple[int, float]:
    info = win32file.GetFileInformationByHandle(handle)
    attributes = info[0]
    if (
        win32file.GetFileType(handle) != win32file.FILE_TYPE_DISK
        or attributes & win32con.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError("Workspace access rejects reparse points and non-disk files")
    if regular and (attributes & win32con.FILE_ATTRIBUTE_DIRECTORY or info[7] != 1):
        raise ValueError("Workspace access requires a regular file without hard links")
    return attributes, info[3].timestamp()


def _read(handle: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        try:
            _, data = win32file.ReadFile(handle, _CHUNK)
        except pywintypes.error as exc:
            if exc.winerror != 38:  # ERROR_HANDLE_EOF
                raise
            data = b""
        if not data:
            return b"".join(chunks)
        chunks.append(data)


def _replace(handle: int, parent: int, name: str) -> None:
    win32file.SetFileInformationByHandle(
        handle,
        win32file.FileRenameInfo,
        {"ReplaceIfExists": True, "RootDirectory": parent, "FileName": name},
    )


def _parts(relative: str) -> list[str]:
    parts = relative.split("/")
    for part in parts:
        validate_windows_component(part)
    return parts


class WorkspaceFiles:
    """Never reopen checked children through the host pathname namespace."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @contextmanager
    def _directory(self, parts: list[str], *, create: bool = False) -> Iterator[int]:
        # Host ancestors of the configured root are trusted. The root itself
        # is opened without following its reparse point and verified below.
        if not self.root.is_absolute() or self.root.drive.startswith("\\\\"):
            raise ValueError("Workspace requires an absolute local Windows drive path")
        with _os_errors(), ExitStack() as stack:
            if win32file.GetDriveType(self.root.anchor) != win32file.DRIVE_FIXED:
                raise ValueError("Workspace requires a fixed local Windows drive")
            root_handle = win32file.CreateFile(
                str(self.root),
                win32con.SYNCHRONIZE | _FILE_READ_ATTRIBUTES | _FILE_LIST_DIRECTORY,
                win32con.FILE_SHARE_READ,
                None,
                win32file.OPEN_EXISTING,
                win32file.FILE_FLAG_BACKUP_SEMANTICS | win32file.FILE_FLAG_OPEN_REPARSE_POINT,
                None,
            )
            handle = int(root_handle.Detach())
            stack.callback(win32file.CloseHandle, handle)
            attributes, _ = _attributes(handle)
            if not attributes & win32con.FILE_ATTRIBUTE_DIRECTORY:
                raise ValueError("Workspace root must be a directory")
            for part in parts:
                handle = open_relative(handle, part, directory=True, create=create)
                stack.callback(win32file.CloseHandle, handle)
                attributes, _ = _attributes(handle)
                if not attributes & win32con.FILE_ATTRIBUTE_DIRECTORY:
                    raise ValueError("Workspace parent must be a directory")
            yield handle

    def read(self, relative: str) -> bytes:
        parts = _parts(relative)
        with self._directory(parts[:-1]) as parent, _os_errors():
            handle = open_relative(parent, parts[-1], directory=False)
            try:
                _attributes(handle, regular=True)
                return _read(handle)
            finally:
                win32file.CloseHandle(handle)

    def write(self, relative: str, data: bytes) -> bool:
        parts = _parts(relative)
        with self._directory(parts[:-1], create=True) as parent, _os_errors():
            try:
                target = open_relative(parent, parts[-1], directory=False)
            except FileNotFoundError:
                existed = False
            else:
                try:
                    _attributes(target, regular=True)
                finally:
                    win32file.CloseHandle(target)
                existed = True
            temporary = f".tlon-write-{secrets.token_hex(16)}"
            handle = open_relative(parent, temporary, directory=False, create=True, write=True)
            renamed = False
            try:
                _attributes(handle, regular=True)
                offset = 0
                while offset < len(data):
                    _, written = win32file.WriteFile(handle, data[offset : offset + _CHUNK])
                    if written == 0:
                        raise OSError("Workspace write made no progress")
                    offset += written
                win32file.FlushFileBuffers(handle)
                _replace(handle, parent, parts[-1])
                renamed = True
            finally:
                try:
                    if not renamed:
                        win32file.SetFileInformationByHandle(
                            handle, win32file.FileDispositionInfo, True
                        )
                finally:
                    win32file.CloseHandle(handle)
            return existed

    def archive(self, relative: str, root_name: str) -> bytes:
        parts = _parts(relative) if relative else []
        buffer = io.BytesIO()
        with (
            self._directory(parts[:-1]) as parent,
            _os_errors(),
            tarfile.open(fileobj=buffer, mode="w") as archive,
        ):
            if parts:
                handle = open_relative(parent, parts[-1])
                try:
                    self._archive_handle(archive, handle, parts[-1])
                finally:
                    win32file.CloseHandle(handle)
            else:
                self._archive_handle(archive, parent, root_name)
        return buffer.getvalue()

    def _archive_handle(self, archive: tarfile.TarFile, handle: int, name: str) -> None:
        attributes, mtime = _attributes(handle)
        entry = tarfile.TarInfo(name)
        entry.mtime = mtime
        if not attributes & win32con.FILE_ATTRIBUTE_DIRECTORY:
            _attributes(handle, regular=True)
            data = _read(handle)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
            return
        entry.type = tarfile.DIRTYPE
        archive.addfile(entry)
        names: list[str] = []
        batch = win32file.GetFileInformationByHandleEx(
            handle, win32file.FileIdBothDirectoryRestartInfo
        )
        while batch:
            names.extend(item["FileName"] for item in batch if item["FileName"] not in {".", ".."})
            batch = win32file.GetFileInformationByHandleEx(
                handle, win32file.FileIdBothDirectoryInfo
            )
        for child in sorted(names):
            child_handle = open_relative(handle, child)
            try:
                self._archive_handle(archive, child_handle, f"{name}/{child}")
            finally:
                win32file.CloseHandle(child_handle)
