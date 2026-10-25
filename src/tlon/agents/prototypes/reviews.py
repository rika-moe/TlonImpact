"""Explicit prototype material reads and durable, advisory review receipts."""

import fcntl
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Any

from tlon.agents.prototypes.info import (
    BEHAVIOR_FILE,
    LESSONS_FILE,
    METADATA_FILE,
    REVIEWS_FILE,
    _read_metadata,
    _validate_name,
)
from tlon.base.paths import TlonPaths
from tlon.evolution.ports import PrototypeError

_RECEIPT_PREFIX = "<!-- tlon-review-receipt "
_VERDICTS = {"通过", "不通过", "弃权"}


@contextmanager
def staging_lock(tlon_paths: TlonPaths) -> Iterator[None]:
    """Serialize review writes and human approval/rejection across processes."""
    root = tlon_paths.prototypes_staging_dir()
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _directory(name: str, tlon_paths: TlonPaths) -> Path:
    _validate_name(name)
    roots = (tlon_paths.prototypes_dir(), tlon_paths.prototypes_staging_dir())
    for root in roots:
        directory = root / name
        if directory.is_symlink():
            raise PrototypeError("prototype directory must not be a symlink")
        if directory.is_dir():
            return directory
    raise PrototypeError(f"no prototype named '{name}'")


def _materials(directory: Path) -> tuple[str, dict[str, str]]:
    paths = [directory / name for name in (METADATA_FILE, BEHAVIOR_FILE, LESSONS_FILE)]
    modules = directory / "modules"
    if modules.is_symlink():
        raise PrototypeError("prototype modules must not be a symlink")
    if modules.exists():
        for path in sorted(modules.rglob("*")):
            if path.is_symlink():
                raise PrototypeError("prototype material must not be a symlink")
            if path.is_file() and path.suffix == ".tlon":
                paths.append(path)
    files: dict[str, str] = {}
    hasher = hashlib.sha256()
    for path in sorted(paths):
        if path.is_symlink():
            raise PrototypeError("prototype material must not be a symlink")
        if not path.exists():
            if path.name == BEHAVIOR_FILE:
                raise PrototypeError("prototype behavior.tlon is missing")
            continue
        if not path.is_file():
            raise PrototypeError("prototype material must be a regular file")
        relative = path.relative_to(directory).as_posix()
        try:
            content = path.read_bytes()
            files[relative] = content.decode("utf-8")
        except (OSError, UnicodeError) as error:
            raise PrototypeError(f"cannot read prototype material: {relative}") from error
        for chunk in (relative.encode(), content):
            hasher.update(len(chunk).to_bytes(8, "big"))
            hasher.update(chunk)
    return hasher.hexdigest(), files


def read_prototype(
    name: str,
    *,
    tlon_paths: TlonPaths,
    path: str | None = None,
    offset: int = 0,
    limit: int = 12000,
    digest: str | None = None,
) -> dict[str, Any]:
    """Read a manifest or a bounded file page from one exact material version."""
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 12000:
        raise PrototypeError("offset must be nonnegative and limit must be between 1 and 12000")
    with staging_lock(tlon_paths):
        directory = _directory(name, tlon_paths)
        current_digest, files = _materials(directory)
        if digest is not None and digest != current_digest:
            raise PrototypeError("prototype material digest changed")
        content: str | None = None
        next_offset: int | None = None
        if path is not None:
            relative = PurePosixPath(path)
            if relative.is_absolute() or ".." in relative.parts or path not in files:
                raise PrototypeError(f"invalid prototype material path: {path!r}")
            source = files[path]
            content = source[offset : offset + limit]
            if offset + limit < len(source):
                next_offset = offset + limit
        info = _read_metadata(directory)
        metadata = {
            key: getattr(info, key)
            for key in (
                "name",
                "display_name",
                "description",
                "trusted",
                "author",
                "parent",
                "generation",
                "proposal_note",
            )
        }
        return {
            "name": name,
            "digest": current_digest,
            "metadata": metadata,
            "files": sorted(files),
            "path": path,
            "content": content,
            "offset": offset,
            "next_offset": next_offset,
        }


def _request(
    name: str, digest: str, verdict: str, reason: str, reviewer_id: str, run_id: str, call_id: str
) -> dict[str, str]:
    _validate_name(name)
    if verdict not in _VERDICTS:
        raise PrototypeError("verdict must be 通过, 不通过 or 弃权")
    if not reason.strip():
        raise PrototypeError("review reason must not be empty")
    if not all((digest, reviewer_id, run_id, call_id)):
        raise PrototypeError("review digest and host identity must not be empty")
    return {
        "name": name,
        "digest": digest,
        "verdict": verdict,
        "reason": reason,
        "reviewer": reviewer_id,
        "run_id": run_id,
        "call_id": call_id,
    }


def _review_text(directory: Path) -> str:
    path = directory / REVIEWS_FILE
    if path.is_symlink():
        raise PrototypeError("review file must not be a symlink")
    try:
        return path.read_text(encoding="utf-8") if path.exists() else "# 评审记录\n"
    except (OSError, UnicodeError) as error:
        raise PrototypeError("cannot read prototype reviews") from error


def _recover(text: str, request: dict[str, str]) -> dict[str, str] | None:
    for line in text.splitlines():
        if not line.startswith(_RECEIPT_PREFIX):
            continue
        try:
            if not line.endswith(" -->"):
                raise ValueError("incomplete receipt")
            receipt = json.loads(line[len(_RECEIPT_PREFIX) : -4])
            if not isinstance(receipt, dict) or set(receipt) != {*request, "created_at"}:
                raise ValueError("invalid receipt fields")
            if not all(isinstance(value, str) for value in receipt.values()):
                raise ValueError("invalid receipt values")
        except (ValueError, TypeError) as error:
            raise PrototypeError("invalid persisted prototype review receipt") from error
        identity = ("reviewer", "run_id", "call_id")
        if all(receipt[key] == request[key] for key in identity):
            if any(receipt[key] != value for key, value in request.items()):
                raise PrototypeError("review call identity already used with different arguments")
            return receipt
    return None


def recover_prototype_review(
    name: str,
    digest: str,
    verdict: str,
    reason: str,
    *,
    reviewer_id: str,
    run_id: str,
    call_id: str,
    tlon_paths: TlonPaths,
) -> dict[str, str] | None:
    """Recover the original receipt even after human approval moved the package."""
    request = _request(name, digest, verdict, reason, reviewer_id, run_id, call_id)
    with staging_lock(tlon_paths):
        try:
            directory = _directory(name, tlon_paths)
        except PrototypeError:
            # Rejection removes the proposal and therefore its review receipts.
            if not any(
                (root / name).exists() or (root / name).is_symlink()
                for root in (
                    tlon_paths.prototypes_dir(),
                    tlon_paths.prototypes_staging_dir(),
                )
            ):
                return None
            raise
        return _recover(_review_text(directory), request)


def submit_prototype_review(
    name: str,
    digest: str,
    verdict: str,
    reason: str,
    *,
    reviewer_id: str,
    run_id: str,
    call_id: str,
    tlon_paths: TlonPaths,
) -> dict[str, str]:
    """Commit an opinion and its recovery receipt in one atomic file replacement."""
    request = _request(name, digest, verdict, reason, reviewer_id, run_id, call_id)
    with staging_lock(tlon_paths):
        directory = _directory(name, tlon_paths)
        text = _review_text(directory)
        existing = _recover(text, request)
        if existing is not None:
            return existing
        if directory.parent != tlon_paths.prototypes_staging_dir():
            raise PrototypeError(f"no staged prototype named '{name}'")
        current_digest, _files = _materials(directory)
        if current_digest != digest:
            raise PrototypeError("prototype material digest changed")
        receipt = {**request, "created_at": datetime.now(UTC).isoformat()}
        encoded = json.dumps(receipt, ensure_ascii=True, sort_keys=True).replace("<", "\\u003c")
        # Indented user text cannot manufacture a top-level receipt marker.
        body = "\n".join("    " + line for line in reason.splitlines())
        entry = (
            f"\n{_RECEIPT_PREFIX}{encoded} -->\n"
            f"## {receipt['created_at']} — {reviewer_id}：{verdict}\n\n"
            f"Run: `{run_id}` · Call: `{call_id}`\n\n"
            f"Material digest: `{digest}`\n\n{body}\n"
        )
        temporary: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=directory, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(text + entry)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, directory / REVIEWS_FILE)
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return receipt
