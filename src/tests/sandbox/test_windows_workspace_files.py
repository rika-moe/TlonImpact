"""Real Windows handle semantics; missing link privileges are failures, not skips."""

import importlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows native filesystem")


@pytest.fixture
def backend() -> ModuleType:
    return importlib.import_module("tlon.sandbox.workspace_files_windows")


def _junction(link: Path, target: Path) -> None:
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, repr(result.stdout + result.stderr)


def _invoke(files: Any, operation: str, path: str) -> Any:
    if operation == "write":
        return files.write(path, b"changed")
    if operation == "archive":
        return files.archive(path, "workspace")
    return files.read(path)


@pytest.mark.parametrize(
    "data",
    [b"", "你好，世界 🐙".encode(), bytes(range(256)) * 32769],
    ids=["empty", "unicode", "8mb"],
)
def test_roundtrip_unicode_nested_and_overwrite(
    backend: ModuleType, tmp_path: Path, data: bytes
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    files = backend.WorkspaceFiles(root)
    name = "资料/多层/🐙.txt"
    assert files.write(name, data) is False
    assert files.read(name) == data
    assert files.write(name, b"replacement") is True
    assert files.read(name) == b"replacement"
    assert files.write(name, data) is True
    with tarfile.open(fileobj=io.BytesIO(files.archive("", "workspace"))) as archive:
        member = archive.extractfile(f"workspace/{name}")
        assert member is not None
        assert member.read() == data
    shutil.rmtree(root)  # No directory or file handles may survive an operation.


def test_archive_exhausts_multiple_directory_batches(backend: ModuleType, tmp_path: Path) -> None:
    # Hundreds of long names cannot fit in a single 2048-byte Windows info buffer.
    expected: dict[str, bytes] = {}
    for index in range(220):
        name = f"记录-{index:04d}-{'x' * 60}.txt"
        data = str(index).encode()
        (tmp_path / name).write_bytes(data)
        expected[f"workspace/{name}"] = data
    files = backend.WorkspaceFiles(tmp_path)
    with tarfile.open(fileobj=io.BytesIO(files.archive("", "workspace"))) as archive:
        names = [member.name for member in archive if member.isfile()]
        assert sorted(names) == sorted(expected)
        for name, data in expected.items():
            member = archive.extractfile(name)
            assert member is not None and member.read() == data


@pytest.mark.parametrize("operation", ["read", "write", "archive"])
@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "a/../secret",
        "/secret",
        "C:/secret",
        "C:secret",
        "//server/share/secret",
        r"\\server\share\secret",
        r"\\?\C:\secret",
        r"\\.\NUL",
        r"a\secret",
        "secret:stream",
        "a/secret:stream",
        "CON",
        "nul.txt",
        "CON .txt",
        "NUL .txt",
        "COM1 .log",
        "PRN",
        "AUX.log",
        "COM1",
        "lpt9.txt",
        "COM¹.txt",
        "LPT²",
        "trailing.",
        "trailing ",
        "a//secret",
        "./secret",
        "a/*",
        "a/?",
        'a/"bad"',
        "a/<bad>",
        "a/pipe|",
        "a/\x01bad",
    ],
)
def test_rejects_windows_path_aliases(
    backend: ModuleType, tmp_path: Path, operation: str, path: str
) -> None:
    (tmp_path / "secret").write_bytes(b"private")
    with pytest.raises((ValueError, OSError)):
        _invoke(backend.WorkspaceFiles(tmp_path), operation, path)
    assert (tmp_path / "secret").read_bytes() == b"private"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["secret"]


@pytest.mark.parametrize("operation", ["read", "write", "archive"])
@pytest.mark.parametrize(
    "kind",
    [
        "root-symlink",
        "root-junction",
        "file-symlink",
        "ancestor-symlink",
        "ancestor-junction",
        "hardlink",
    ],
)
def test_links_cannot_expose_external_files(
    backend: ModuleType, tmp_path: Path, operation: str, kind: str
) -> None:
    root, outside = tmp_path / "own", tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "secret"
    sentinel.write_bytes(b"private")
    path = "secret"
    if kind == "root-symlink":
        root.symlink_to(outside, target_is_directory=True)
    elif kind == "root-junction":
        _junction(root, outside)
    else:
        root.mkdir()
        if kind == "file-symlink":
            (root / path).symlink_to(sentinel)
        elif kind == "hardlink":
            os.link(sentinel, root / path)
        else:
            if kind == "ancestor-symlink":
                (root / "link").symlink_to(outside, target_is_directory=True)
            else:
                _junction(root / "link", outside)
            path = "link/secret"
    with pytest.raises((ValueError, OSError)):
        _invoke(backend.WorkspaceFiles(root), operation, path)
    # Traversal must also reject links discovered during recursive enumeration.
    if kind not in {"root-symlink", "root-junction"}:
        with pytest.raises((ValueError, OSError)):
            backend.WorkspaceFiles(root).archive("", "workspace")
    assert sentinel.read_bytes() == b"private"
    if kind == "root-symlink":
        root.unlink()
    elif kind == "root-junction":
        root.rmdir()
    else:
        if kind == "ancestor-symlink":
            (root / "link").unlink()
        elif kind == "ancestor-junction":
            (root / "link").rmdir()
        shutil.rmtree(root)
    assert sentinel.read_bytes() == b"private"


def test_held_ancestor_cannot_be_renamed_into_external_link(
    backend: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, outside = tmp_path / "own", tmp_path / "outside"
    ancestor = root / "level"
    parent = ancestor / "parent"
    parent.mkdir(parents=True)
    outside.mkdir()
    (outside / "note").write_bytes(b"private")
    original = backend._replace
    attempted = False

    def attempt_escape(handle: Any, parent_handle: Any, name: str) -> None:
        nonlocal attempted
        attempted = True
        with pytest.raises(OSError):
            parent.rename(root / "moved")
        with pytest.raises(OSError):
            ancestor.rename(root / "moved-ancestor")
        with pytest.raises(OSError):
            root.rename(tmp_path / "moved-root")
        original(handle, parent_handle, name)

    monkeypatch.setattr(backend, "_replace", attempt_escape)
    backend.WorkspaceFiles(root).write("level/parent/note", b"new")
    assert attempted
    assert (parent / "note").read_bytes() == b"new"
    assert (outside / "note").read_bytes() == b"private"
    shutil.rmtree(root)


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
def test_target_substitution_cannot_modify_external_inode(
    backend: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    root = tmp_path / "own"
    root.mkdir()
    target, outside = root / "note", tmp_path / "secret"
    target.write_bytes(b"old")
    outside.write_bytes(b"private")
    # Prepare the attack before workspace handles deny concurrent mutations.
    # A creation failure here is a test setup error, never a security success.
    prepared = tmp_path / "prepared-link"
    if kind == "symlink":
        prepared.symlink_to(outside)
    else:
        os.link(outside, prepared)
    assert prepared.samefile(outside)
    original = backend._replace
    outcome: str | None = None

    def swap_target(handle: Any, parent: Any, name: str) -> None:
        nonlocal outcome
        try:
            os.replace(prepared, target)
        except OSError as exc:
            if getattr(exc, "winerror", None) != 32:
                raise AssertionError("unexpected error arranging target substitution") from exc
            outcome = "prevented-by-sharing"
        else:
            outcome = "swapped"
        original(handle, parent, name)

    monkeypatch.setattr(backend, "_replace", swap_target)
    try:
        backend.WorkspaceFiles(root).write("note", b"new")
    except (ValueError, OSError):
        # A refusal is acceptable only after the malicious entry was installed.
        # If Windows blocked the attacker, the ordinary write must still work.
        assert outcome == "swapped"
    assert outcome in {"swapped", "prevented-by-sharing"}
    if outcome == "prevented-by-sharing":
        assert prepared.samefile(outside)
        assert (root / "note").read_bytes() == b"new"
    assert outside.read_bytes() == b"private"
    assert not list(root.glob(".tlon-write-*"))
    shutil.rmtree(root)


def test_open_temporary_cannot_be_replaced_by_external_link(
    backend: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "own"
    root.mkdir()
    outside = tmp_path / "secret"
    outside.write_bytes(b"private")
    original = backend._replace
    attempted = False

    def steal_source(handle: Any, parent: Any, name: str) -> None:
        nonlocal attempted
        attempted = True
        temporary = Path(backend.win32file.GetFinalPathNameByHandle(handle, 0))
        with pytest.raises(OSError):
            temporary.rename(root / "stolen")
        with pytest.raises(OSError):
            temporary.unlink()
        original(handle, parent, name)

    monkeypatch.setattr(backend, "_replace", steal_source)
    backend.WorkspaceFiles(root).write("note", b"new")
    assert attempted
    assert (root / "note").read_bytes() == b"new"
    assert outside.read_bytes() == b"private"
    assert sorted(path.name for path in root.iterdir()) == ["note"]
    shutil.rmtree(root)


def test_failed_replace_removes_temporary_and_releases_handles(
    backend: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "own"
    root.mkdir()
    (root / "note").write_bytes(b"old")

    def fail(handle: Any, parent: Any, name: str) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(backend, "_replace", fail)
    with pytest.raises(OSError, match="injected replace failure"):
        backend.WorkspaceFiles(root).write("note", b"new")
    assert sorted(path.name for path in root.iterdir()) == ["note"]
    assert (root / "note").read_bytes() == b"old"
    shutil.rmtree(root)
