"""Tests for DockerRuntime: file cabinet creation and ephemeral execution units."""

import io
import tarfile
import threading
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from docker.errors import NotFound

from tlon.base.config import DockerRuntimeConfig
from tlon.base.paths import TlonPaths
from tlon.sandbox.docker_runtime import (
    AGENT_HOME_CONTAINER_PATH,
    TMPFS_MOUNTS,
    DockerRuntime,
    _container_matches,
    _container_mismatches,
    _ContainerSpec,
)


def _make_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> DockerRuntime:
    monkeypatch.setattr(DockerRuntime, "_create_client", lambda self: MagicMock())
    return DockerRuntime(
        DockerRuntimeConfig(), agent_id="dev-001", tlon_paths=TlonPaths(home=tmp_path)
    )


def _matching_attrs(spec: _ContainerSpec) -> dict[str, Any]:
    """A docker inspect payload that matches the spec on every dimension."""
    return {
        "Config": {"Image": spec.image},
        "HostConfig": {
            "Binds": sorted(spec.binds),
            "Memory": spec.memory_bytes,
            "CpuPeriod": spec.cpu_period,
            "CpuQuota": spec.cpu_quota,
            "NetworkMode": spec.network_mode,
            "ReadonlyRootfs": spec.read_only,
            "Tmpfs": dict(spec.tmpfs),
        },
    }


class TestCreateContainerMounts:
    async def test_agent_home_mounted_read_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(DockerRuntime, "_create_client", lambda self: MagicMock())
        tlon_paths = TlonPaths(home=tmp_path)

        runtime = DockerRuntime(DockerRuntimeConfig(), agent_id="dev-001", tlon_paths=tlon_paths)
        runtime.client.images.get = MagicMock()
        runtime.client.containers.create = MagicMock(return_value=MagicMock())

        await runtime._create_container()

        volumes = runtime.client.containers.create.call_args.kwargs["volumes"]
        workspace = tlon_paths.agent_workspace_dir("dev-001")
        home = tlon_paths.agent_dir("dev-001")
        assert volumes == {
            str(workspace): {"bind": "/workspace", "mode": "rw"},
            str(home): {"bind": AGENT_HOME_CONTAINER_PATH, "mode": "ro"},
        }

    async def test_cabinet_is_created_never_started(self, tmp_path, monkeypatch):
        """The file cabinet serves archive I/O only — created, never started."""
        runtime = _make_runtime(monkeypatch, tmp_path)
        runtime.client.images.get = MagicMock()
        cabinet = MagicMock()
        runtime.client.containers.create = MagicMock(return_value=cabinet)

        await runtime._create_container()

        assert runtime.container is cabinet
        cabinet.start.assert_not_called()


class TestCreateContainerHardening:
    async def test_read_only_rootfs_and_tmpfs(self, tmp_path, monkeypatch):
        runtime = _make_runtime(monkeypatch, tmp_path)
        runtime.client.images.get = MagicMock()
        runtime.client.containers.create = MagicMock(return_value=MagicMock())

        await runtime._create_container()

        kwargs = runtime.client.containers.create.call_args.kwargs
        assert kwargs["read_only"] is True
        assert kwargs["tmpfs"] == TMPFS_MOUNTS == {"/tmp": "rw,nosuid,nodev,size=64m"}


class TestContainerMatches:
    def test_full_match(self, tmp_path, monkeypatch):
        spec = _make_runtime(monkeypatch, tmp_path)._expected_spec()
        assert _container_matches(_matching_attrs(spec), spec)
        assert _container_mismatches(_matching_attrs(spec), spec) == []

    @pytest.mark.parametrize(
        "dimension",
        ["image", "binds", "mem_limit", "cpu", "network_mode", "read_only", "tmpfs"],
    )
    def test_each_dimension_mismatch_detected(self, dimension, tmp_path, monkeypatch):
        spec = _make_runtime(monkeypatch, tmp_path)._expected_spec()
        attrs = _matching_attrs(spec)
        host_config = attrs["HostConfig"]
        if dimension == "image":
            attrs["Config"]["Image"] = "ubuntu:24.04"
        elif dimension == "binds":
            host_config["Binds"] = host_config["Binds"][:-1]
        elif dimension == "mem_limit":
            host_config["Memory"] += 1
        elif dimension == "cpu":
            host_config["CpuQuota"] = 1
        elif dimension == "network_mode":
            host_config["NetworkMode"] = "host"
        elif dimension == "read_only":
            host_config["ReadonlyRootfs"] = False
        elif dimension == "tmpfs":
            host_config["Tmpfs"] = {}

        assert not _container_matches(attrs, spec)
        assert _container_mismatches(attrs, spec) == [dimension]

    def test_legacy_container_without_hardening_fields_mismatches(self, tmp_path, monkeypatch):
        spec = _make_runtime(monkeypatch, tmp_path)._expected_spec()
        attrs = _matching_attrs(spec)
        del attrs["HostConfig"]["ReadonlyRootfs"]
        del attrs["HostConfig"]["Tmpfs"]

        assert not _container_matches(attrs, spec)
        assert _container_mismatches(attrs, spec) == ["read_only", "tmpfs"]


class TestEnsureRunningReconcile:
    async def test_drift_triggers_remove_and_recreate(self, tmp_path, monkeypatch):
        runtime = _make_runtime(monkeypatch, tmp_path)
        spec = runtime._expected_spec()
        attrs = _matching_attrs(spec)
        attrs["HostConfig"]["ReadonlyRootfs"] = False  # legacy container, pre-hardening
        container = MagicMock(attrs=attrs)
        runtime.client.containers.get = MagicMock(return_value=container)
        create = AsyncMock()
        monkeypatch.setattr(DockerRuntime, "_create_container", create)

        await runtime.ensure_running()

        container.remove.assert_called_once_with(force=True)
        create.assert_awaited_once()
        container.start.assert_not_called()

    async def test_matching_cabinet_is_used_as_is(self, tmp_path, monkeypatch):
        runtime = _make_runtime(monkeypatch, tmp_path)
        container = MagicMock(attrs=_matching_attrs(runtime._expected_spec()))
        runtime.client.containers.get = MagicMock(return_value=container)
        create = AsyncMock()
        monkeypatch.setattr(DockerRuntime, "_create_container", create)

        await runtime.ensure_running()

        assert runtime.container is container
        container.remove.assert_not_called()
        container.start.assert_not_called()
        create.assert_not_awaited()

    async def test_stopped_cabinet_is_adopted_never_started(self, tmp_path, monkeypatch):
        """A created-but-stopped cabinet is fine: archive I/O works on it."""
        runtime = _make_runtime(monkeypatch, tmp_path)
        container = MagicMock(attrs=_matching_attrs(runtime._expected_spec()), status="exited")
        runtime.client.containers.get = MagicMock(return_value=container)
        create = AsyncMock()
        monkeypatch.setattr(DockerRuntime, "_create_container", create)

        await runtime.ensure_running()

        assert runtime.container is container
        container.start.assert_not_called()
        container.remove.assert_not_called()
        create.assert_not_awaited()

    async def test_missing_container_is_created(self, tmp_path, monkeypatch):
        runtime = _make_runtime(monkeypatch, tmp_path)
        runtime.client.containers.get = MagicMock(side_effect=NotFound("no such container"))
        create = AsyncMock()
        monkeypatch.setattr(DockerRuntime, "_create_container", create)

        await runtime.ensure_running()

        create.assert_awaited_once()


def _runtime_with_exec_unit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    wait_result: dict[str, Any] | None = None,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> tuple[DockerRuntime, MagicMock]:
    """Runtime whose client.containers.create returns a fake exec unit."""
    runtime = _make_runtime(monkeypatch, tmp_path)
    exec_unit = MagicMock()
    if wait_result is not None:
        exec_unit.wait.return_value = wait_result
    exec_unit.logs.side_effect = lambda **kwargs: stdout if kwargs["stdout"] else stderr
    runtime.client.containers.create = MagicMock(return_value=exec_unit)
    return runtime, exec_unit


class TestRunEphemeral:
    async def test_exec_unit_shares_creation_params_with_cabinet(self, tmp_path, monkeypatch):
        """Single source: exec unit kwargs equal the cabinet kwargs on every
        shared dimension (image/mounts/limits/ro rootfs/tmpfs/network)."""
        runtime, _ = _runtime_with_exec_unit(monkeypatch, tmp_path, wait_result={"StatusCode": 0})

        await runtime.run("echo hi")

        exec_kwargs = runtime.client.containers.create.call_args.kwargs
        cabinet_kwargs = runtime._container_kwargs(
            name="tlon-agent-dev-001", command=["sleep", "infinity"]
        )
        for key in (
            "image",
            "working_dir",
            "volumes",
            "mem_limit",
            "cpu_period",
            "cpu_quota",
            "network_mode",
            "read_only",
            "tmpfs",
        ):
            assert exec_kwargs[key] == cabinet_kwargs[key]
        assert exec_kwargs["read_only"] is True
        assert exec_kwargs["tmpfs"] == TMPFS_MOUNTS
        assert exec_kwargs["network_mode"] == "none"
        assert exec_kwargs["name"].startswith("tlon-agent-dev-001-exec-")
        assert exec_kwargs["command"] == ["timeout", "60", "bash", "-c", "echo hi"]

    async def test_run_returns_exit_code_and_demuxed_output(self, tmp_path, monkeypatch):
        runtime, _ = _runtime_with_exec_unit(
            monkeypatch, tmp_path, wait_result={"StatusCode": 3}, stdout=b"out\n", stderr=b"err\n"
        )

        result = await runtime.run("some command")

        assert result.exit_code == 3
        assert result.stdout == "out\n"
        assert result.stderr == "err\n"

    async def test_workdir_override_reaches_create(self, tmp_path, monkeypatch):
        runtime, _ = _runtime_with_exec_unit(monkeypatch, tmp_path, wait_result={"StatusCode": 0})

        await runtime.run("pwd", workdir="/tmp")

        assert runtime.client.containers.create.call_args.kwargs["working_dir"] == "/tmp"

    async def test_exec_unit_is_removed_after_success(self, tmp_path, monkeypatch):
        runtime, exec_unit = _runtime_with_exec_unit(
            monkeypatch, tmp_path, wait_result={"StatusCode": 0}
        )

        await runtime.run("echo hi")

        exec_unit.remove.assert_called_once_with(force=True)

    async def test_exec_unit_is_removed_on_error(self, tmp_path, monkeypatch):
        runtime, exec_unit = _runtime_with_exec_unit(monkeypatch, tmp_path)
        exec_unit.wait.side_effect = RuntimeError("daemon gone")

        result = await runtime.run("echo hi")

        assert result.exit_code == -1
        assert "daemon gone" in result.stderr
        exec_unit.remove.assert_called_once_with(force=True)

    async def test_exec_unit_is_removed_on_outer_timeout(self, tmp_path, monkeypatch):
        """wait_for backstop fires → finally removes (SIGKILLs) the unit,
        timeout message contract preserved."""
        runtime, exec_unit = _runtime_with_exec_unit(monkeypatch, tmp_path)
        release = threading.Event()
        exec_unit.wait.side_effect = lambda **kwargs: release.wait(30) and {"StatusCode": 0}
        try:
            result = await runtime.run("sleep 999", timeout=1)
        finally:
            release.set()  # unblock the abandoned wait thread promptly

        assert result.exit_code == -1
        assert result.stdout == ""
        assert result.stderr == "Command timed out after 1 seconds"
        exec_unit.remove.assert_called_once_with(force=True)

    async def test_exit_124_maps_to_timeout_semantics(self, tmp_path, monkeypatch):
        """The in-container `timeout` command kills with exit 124 → same
        timeout contract as the outer backstop, stdout preserved."""
        runtime, exec_unit = _runtime_with_exec_unit(
            monkeypatch, tmp_path, wait_result={"StatusCode": 124}, stdout=b"partial\n"
        )

        result = await runtime.run("sleep 999", timeout=2)

        assert result.exit_code == -1
        assert result.stdout == "partial\n"
        assert result.stderr == "Command timed out after 2 seconds"
        exec_unit.remove.assert_called_once_with(force=True)

    async def test_run_never_touches_the_cabinet(self, tmp_path, monkeypatch):
        """run() spins its own unit — no ensure_running, no cabinet lookup."""
        runtime, _ = _runtime_with_exec_unit(monkeypatch, tmp_path, wait_result={"StatusCode": 0})

        result = await runtime.run("echo hi")

        assert result.exit_code == 0
        assert runtime.container is None
        runtime.client.containers.get.assert_not_called()


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

    async def test_utf8_text_round_trips(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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
