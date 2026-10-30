"""Exercise catalog exclusion with real independent processes."""

import subprocess
import sys
from pathlib import Path

import pytest

from tlon.agents.prototypes.reviews import staging_lock
from tlon.base.directory_lock import directory_lock
from tlon.base.paths import TlonPaths
from tlon.evolution.publication import publish_directory

_PUBLISH = """
import sys
from pathlib import Path
from tlon.evolution.publication import publish_directory
print("ready", flush=True)
try:
    publish_directory(Path(sys.argv[1]), {"payload": "child"})
except FileExistsError:
    sys.exit(3)
"""


def test_review_lock_blocks_publication_across_processes(tmp_path: Path) -> None:
    paths = TlonPaths(home=tmp_path)
    target = paths.prototypes_staging_dir() / "proposal"
    process: subprocess.Popen[str] | None = None
    try:
        with staging_lock(paths):
            process = subprocess.Popen(
                [sys.executable, "-c", _PUBLISH, str(target)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert process.stdout is not None
            assert process.stdout.readline().strip() == "ready"
            with pytest.raises(subprocess.TimeoutExpired):
                process.communicate(timeout=1)
            assert not target.exists()
            publish_directory(paths.prototypes_dir() / "trusted", {"payload": "parent"})
        _, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr
        assert (target / "payload").read_text() == "child"
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate()


def test_abrupt_process_exit_releases_directory_lock(tmp_path: Path) -> None:
    directory = tmp_path / "catalog"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sys; from pathlib import Path; "
            "from tlon.base.directory_lock import directory_lock; "
            "lock = directory_lock(Path(sys.argv[1])); lock.__enter__(); os._exit(7)",
            str(directory),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 7, result.stderr
    result = subprocess.run(
        [sys.executable, "-c", _PUBLISH, str(directory / "proposal")],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_concurrent_process_publication_never_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "catalog" / "proposal"
    processes: list[subprocess.Popen[str]] = []
    try:
        with directory_lock(target.parent):
            for _ in range(2):
                processes.append(
                    subprocess.Popen(
                        [sys.executable, "-c", _PUBLISH, str(target)],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                )
        for process in processes:
            process.communicate(timeout=15)
        assert sorted(process.returncode for process in processes) == [0, 3]
        assert (target / "payload").read_text() == "child"
        assert [path.name for path in target.parent.iterdir()] == ["proposal"]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate()
