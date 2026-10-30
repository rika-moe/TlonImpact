"""Cross-process locks for host-managed catalogs, outside catalog contents."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock


@contextmanager
def directory_lock(directory: Path) -> Iterator[None]:
    """Lock a canonical catalog directory; callers must not acquire it recursively."""
    directory.mkdir(parents=True, exist_ok=True)
    directory = directory.resolve()
    lock_directory = directory.parent / ".tlon-locks"
    lock_directory.mkdir(exist_ok=True)
    # Both review and publication use this identity. Let filelock own the lock
    # file lifecycle: unlinking it ourselves can break cross-process exclusion.
    with FileLock(lock_directory / f"{directory.name}.lock"):
        yield
