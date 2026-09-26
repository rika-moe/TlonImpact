"""Tests for DockerRuntime container creation (bind mounts)."""

import io
import tarfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from docker.errors import NotFound

from tlon.base.config import DockerRuntimeConfig
from tlon.base.paths import TlonPaths
from tlon.sandbox.docker_runtime import AGENT_HOME_CONTAINER_PATH, DockerRuntime


class TestCreateContainerMounts:
    async def test_agent_home_mounted_read_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(DockerRuntime, "_create_client", lambda self: MagicMock())
        tlon_paths = TlonPaths(home=tmp_path)

        runtime = DockerRuntime(DockerRuntimeConfig(), agent_id="dev-001", tlon_paths=tlon_paths)
        runtime.client.images.get = MagicMock()
        runtime.client.containers.run = MagicMock(return_value=MagicMock())

        await runtime._create_container()

        volumes = runtime.client.containers.run.call_args.kwargs["volumes"]
        workspace = tlon_paths.agent_workspace_dir("dev-001")
        home = tlon_paths.agent_dir("dev-001")
        assert volumes == {
            str(workspace): {"bind": "/workspace", "mode": "rw"},
            str(home): {"bind": AGENT_HOME_CONTAINER_PATH, "mode": "ro"},
        }


class TestClose:
    def test_close_closes_underlying_client(self, monkeypatch, tmp_path):
        monkeypatch.setattr(DockerRuntime, "_create_client", lambda self: MagicMock())
        runtime = DockerRuntime(
            DockerRuntimeConfig(), agent_id="dev-001", tlon_paths=TlonPaths(home=tmp_path)
        )

        runtime.close()

        runtime.client.close.assert_called_once()

    def test_close_is_idempotent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(DockerRuntime, "_create_client", lambda self: MagicMock())
        runtime = DockerRuntime(
            DockerRuntimeConfig(), agent_id="dev-001", tlon_paths=TlonPaths(home=tmp_path)
        )

        runtime.close()
        runtime.close()

        runtime.client.close.assert_called_once()


def _make_tar(members: list[tuple[str, bytes | None]]) -> bytes:
    """Build a tar archive in memory; content None means a directory member."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, content in members:
            if content is None:
                info = tarfile.TarInfo(name=name)
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info = tarfile.TarInfo(name=name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _runtime_with_archive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tar_bytes: bytes, chunk_size: int = 0
) -> DockerRuntime:
    monkeypatch.setattr(DockerRuntime, "_create_client", lambda self: MagicMock())
    tlon_paths = TlonPaths(home=tmp_path)
    runtime = DockerRuntime(DockerRuntimeConfig(), agent_id="dev-001", tlon_paths=tlon_paths)
    container = MagicMock()
    if chunk_size > 0:
        chunks = [tar_bytes[i : i + chunk_size] for i in range(0, len(tar_bytes), chunk_size)]
    else:
        chunks = [tar_bytes]
    container.get_archive.return_value = (iter(chunks), {"name": "unused"})
    runtime.container = container
    return runtime


class TestReadFile:
    async def test_binary_content_round_trips_byte_for_byte(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        payload = bytes(range(256))
        tar_bytes = _make_tar([("image.bin", payload)])
        runtime = _runtime_with_archive(monkeypatch, tmp_path, tar_bytes, chunk_size=64)

        assert await runtime.read_file("/workspace/image.bin") == payload

    async def test_utf8_text_round_trips(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        payload = "你好，世界\nhello\n".encode()
        tar_bytes = _make_tar([("notes.txt", payload)])
        runtime = _runtime_with_archive(monkeypatch, tmp_path, tar_bytes)

        assert await runtime.read_file("/workspace/notes.txt") == payload

    async def test_missing_path_raises_file_not_found(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        monkeypatch.setattr(DockerRuntime, "_create_client", lambda self: MagicMock())
        runtime = DockerRuntime(
            DockerRuntimeConfig(), agent_id="dev-001", tlon_paths=TlonPaths(home=tmp_path)
        )
        container = MagicMock()
        container.get_archive.side_effect = NotFound("no such file")
        runtime.container = container

        with pytest.raises(FileNotFoundError):
            await runtime.read_file("/workspace/missing.txt")

    async def test_directory_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        tar_bytes = _make_tar([("somedir", None), ("somedir/inner.txt", b"inner")])
        runtime = _runtime_with_archive(monkeypatch, tmp_path, tar_bytes)

        with pytest.raises(RuntimeError, match="directory"):
            await runtime.read_file("/workspace/somedir")

    async def test_member_missing_from_archive_raises_runtime_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        tar_bytes = _make_tar([("other.txt", b"other")])
        runtime = _runtime_with_archive(monkeypatch, tmp_path, tar_bytes)

        with pytest.raises(RuntimeError, match="directory"):
            await runtime.read_file("/workspace/target.txt")
