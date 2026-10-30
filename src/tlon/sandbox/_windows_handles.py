"""NT primitives for directory-handle-relative open and replacement."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from tlon.sandbox.windows_paths import validate_windows_component

_FILE_OPEN = 1
_FILE_CREATE = 2
_FILE_OPEN_IF = 3
_FILE_DIRECTORY_FILE = 0x1
_FILE_NON_DIRECTORY_FILE = 0x40
_FILE_SYNCHRONOUS_IO_NONALERT = 0x20
_FILE_OPEN_REPARSE_POINT = 0x00200000
_FILE_READ_DATA = 0x1  # Also FILE_LIST_DIRECTORY for directory handles.
_FILE_WRITE_DATA = 0x2
_FILE_READ_ATTRIBUTES = 0x80
_FILE_ATTRIBUTE_NORMAL = 0x80
_FILE_SHARE_READ = 0x1
_SYNCHRONIZE = 0x00100000
_DELETE = 0x00010000
_OBJ_CASE_INSENSITIVE = 0x40


assert sys.platform == "win32", "Windows native module"


class _UnicodeString(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.USHORT),
        ("MaximumLength", wintypes.USHORT),
        ("Buffer", wintypes.LPWSTR),
    ]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("Length", wintypes.ULONG),
        ("RootDirectory", wintypes.HANDLE),
        ("ObjectName", ctypes.POINTER(_UnicodeString)),
        ("Attributes", wintypes.ULONG),
        ("SecurityDescriptor", ctypes.c_void_p),
        ("SecurityQualityOfService", ctypes.c_void_p),
    ]


class _Status(ctypes.Union):
    _fields_ = [("Status", wintypes.LONG), ("Pointer", ctypes.c_void_p)]


class _IoStatus(ctypes.Structure):
    _fields_ = [("Status", _Status), ("Information", ctypes.c_size_t)]


class _RenameInformation(ctypes.Structure):
    _fields_ = [
        ("ReplaceIfExists", wintypes.BYTE),
        ("RootDirectory", wintypes.HANDLE),
        ("FileNameLength", wintypes.ULONG),
        ("FileName", wintypes.WCHAR * 1),
    ]


_ntdll = ctypes.WinDLL("ntdll")
_create = _ntdll.NtCreateFile
_create.argtypes = [
    ctypes.POINTER(wintypes.HANDLE),
    wintypes.ULONG,
    ctypes.POINTER(_ObjectAttributes),
    ctypes.POINTER(_IoStatus),
    ctypes.c_void_p,
    wintypes.ULONG,
    wintypes.ULONG,
    wintypes.ULONG,
    wintypes.ULONG,
    ctypes.c_void_p,
    wintypes.ULONG,
]
_create.restype = wintypes.LONG
_set_information = _ntdll.NtSetInformationFile
_set_information.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_IoStatus),
    ctypes.c_void_p,
    wintypes.ULONG,
    wintypes.ULONG,
]
_set_information.restype = wintypes.LONG
_error = _ntdll.RtlNtStatusToDosError
_error.argtypes = [wintypes.LONG]
_error.restype = wintypes.ULONG


def open_relative(
    parent: int,
    name: str,
    *,
    directory: bool | None = None,
    create: bool = False,
    write: bool = False,
) -> int:
    """Open one component without reparsing, retaining native handle ownership."""
    validate_windows_component(name)
    encoded_length = len(name.encode("utf-16-le"))
    if encoded_length > 65532:
        raise ValueError("Windows path component is too long")
    buffer = ctypes.create_unicode_buffer(name)
    unicode_name = _UnicodeString(
        encoded_length, encoded_length + 2, ctypes.cast(buffer, wintypes.LPWSTR)
    )
    attributes = _ObjectAttributes(
        ctypes.sizeof(_ObjectAttributes),
        int(parent),
        ctypes.pointer(unicode_name),
        _OBJ_CASE_INSENSITIVE,
        None,
        None,
    )
    # Synchronous access, metadata, and data/list-directory access. Temporary
    # files additionally need DELETE for handle-relative rename and cleanup.
    access = (
        _SYNCHRONIZE
        | _FILE_READ_ATTRIBUTES
        | (_FILE_WRITE_DATA | _DELETE if write else _FILE_READ_DATA)
    )
    options = _FILE_SYNCHRONOUS_IO_NONALERT | _FILE_OPEN_REPARSE_POINT
    if directory is not None:
        options |= _FILE_DIRECTORY_FILE if directory else _FILE_NON_DIRECTORY_FILE
    disposition = (_FILE_OPEN_IF if directory else _FILE_CREATE) if create else _FILE_OPEN
    handle = wintypes.HANDLE()
    status = _IoStatus()
    result = _create(
        ctypes.byref(handle),
        access,
        ctypes.byref(attributes),
        ctypes.byref(status),
        None,
        _FILE_ATTRIBUTE_NORMAL,
        _FILE_SHARE_READ,
        disposition,
        options,
        None,
        0,
    )
    if result < 0:
        raise ctypes.WinError(_error(result))
    assert handle.value is not None
    return handle.value


def replace_relative(handle: int, name: str) -> None:
    """Rename within the held source file's directory using a single leaf.

    FILE_RENAME_INFORMATION documents a null RootDirectory plus a simple
    filename as same-directory rename. The source handle remains open and its
    parent chain denies deletion, so the destination cannot be redirected.
    Supplying RootDirectory instead makes Windows reopen the target directory
    for write access, conflicting with those retained protective handles.
    """
    validate_windows_component(name)
    encoded = name.encode("utf-16-le")
    if len(encoded) > 65532:
        raise ValueError("Windows path component is too long")
    buffer = ctypes.create_string_buffer(ctypes.sizeof(_RenameInformation) + len(encoded))
    information = _RenameInformation.from_buffer(buffer)
    information.ReplaceIfExists = 1
    information.RootDirectory = None
    information.FileNameLength = len(encoded)
    ctypes.memmove(
        ctypes.addressof(buffer) + _RenameInformation.FileName.offset, encoded, len(encoded)
    )
    status = _IoStatus()
    result = _set_information(
        handle,
        ctypes.byref(status),
        buffer,
        len(buffer),
        10,  # FileRenameInformation
    )
    if result < 0:
        raise ctypes.WinError(_error(result))
