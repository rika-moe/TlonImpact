"""Owned container reuse is strict and never repairs by deleting."""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tlon.base.paths import TlonPaths
from tlon.gitea.docker_service import ensure_container
from tlon.gitea.storage import GiteaError


def first_container(paths: TlonPaths) -> tuple[MagicMock, MagicMock]:
    import docker

    client = MagicMock()
    client.networks.get.side_effect = docker.errors.NotFound("missing")
    client.networks.create.return_value.name = "tlon-gitea-example-network"
    client.containers.get.side_effect = docker.errors.NotFound("missing")
    container = MagicMock()

    def create(image: str, **kwargs: object) -> MagicMock:
        container.attrs = {
            "Config": {
                "Image": image,
                "Labels": {**kwargs["labels"], "oci.label": "upstream"},
                "User": kwargs["user"],
                "Env": [f"{k}={v}" for k, v in kwargs["environment"].items()],
            },
            "Mounts": [
                {"Source": k, "Destination": v["bind"], "RW": True}
                for k, v in kwargs["volumes"].items()
            ],
            "HostConfig": {
                "PortBindings": {"3000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3000"}]}
            },
            "NetworkSettings": {"Networks": {"tlon-gitea-example-network": {}}},
        }
        container.status = "created"
        return container

    client.containers.create.side_effect = create
    with patch("tlon.gitea.docker_service.verify_mounts"):
        ensure_container(client, paths, "example", 3000)
    client.networks.get.side_effect = None
    client.networks.get.return_value.attrs = {
        "Labels": {
            "io.tlon.gitea.instance": "example",
            "io.tlon.gitea.home": str(paths.gitea_dir().resolve()),
        },
        "Driver": "bridge",
    }
    client.networks.get.return_value.name = "tlon-gitea-example-network"
    client.containers.get.side_effect = None
    client.containers.get.return_value = container
    return client, container


def test_created_stopped_and_running_reuse(tmp_path: Path) -> None:
    paths = TlonPaths(tmp_path)
    paths.gitea_dir().mkdir(parents=True)
    client, container = first_container(paths)
    assert container.start.call_count == 1
    container.status = "exited"
    with patch("tlon.gitea.docker_service.verify_mounts"):
        ensure_container(client, paths, "example", 3000)
    assert container.start.call_count == 2
    container.status = "running"
    with patch("tlon.gitea.docker_service.verify_mounts"):
        ensure_container(client, paths, "example", 3000)
    assert container.start.call_count == 2
    assert client.containers.create.call_count == 1
    container.remove.assert_not_called()


@pytest.mark.parametrize("field", ["image", "labels", "mount", "port", "network", "environment"])
def test_conflicts_do_not_repair(tmp_path: Path, field: str) -> None:
    paths = TlonPaths(tmp_path)
    paths.gitea_dir().mkdir(parents=True)
    client, container = first_container(paths)
    attrs = container.attrs
    if field == "image":
        attrs["Config"]["Image"] = "other"
    elif field == "labels":
        attrs["Config"]["Labels"]["io.tlon.gitea.instance"] = "other"
    elif field == "mount":
        attrs["Mounts"][0]["Source"] = "/other"
    elif field == "port":
        attrs["HostConfig"]["PortBindings"]["3000/tcp"][0]["HostIp"] = "0.0.0.0"
    elif field == "network":
        attrs["NetworkSettings"]["Networks"]["foreign"] = {}
    else:
        attrs["Config"]["Env"] = []
    with pytest.raises(GiteaError):
        ensure_container(client, paths, "example", 3000)
    assert container.start.call_count == 1
    container.remove.assert_not_called()


def test_unshared_mount_rejected_and_probe_cleaned(tmp_path: Path) -> None:
    from tlon.gitea.docker_service import verify_mounts

    paths = TlonPaths(tmp_path)
    paths.gitea_data_dir().mkdir(parents=True)
    paths.gitea_config_dir().mkdir()
    client = MagicMock()
    probe = client.containers.create.return_value
    probe.wait.return_value = {"StatusCode": 1}
    with pytest.raises(GiteaError, match="not shared/writable"):
        verify_mounts(client, paths, "1000:1000", {}, {})
    probe.remove.assert_called_once_with(force=True)
    assert list(paths.gitea_data_dir().iterdir()) == []


def test_shared_mount_probe_roundtrip(tmp_path: Path) -> None:
    from tlon.gitea.docker_service import verify_mounts

    paths = TlonPaths(tmp_path)
    paths.gitea_data_dir().mkdir(parents=True)
    paths.gitea_config_dir().mkdir()
    client = MagicMock()

    def roundtrip(**kwargs: object) -> dict[str, int]:
        for directory in (paths.gitea_data_dir(), paths.gitea_config_dir()):
            marker = next(directory.glob(".tlon-probe-*"))
            nonce = marker.read_text()
            (directory / f".tlon-probe-{nonce}-reply").write_text(nonce)
        return {"StatusCode": 0}

    client.containers.create.return_value.wait.side_effect = roundtrip
    verify_mounts(client, paths, "1000:1000", {}, {})
    command = client.containers.create.call_args.kwargs["command"]
    assert isinstance(command, list) and len(command) == 1
    assert "&&" in command[0]
    assert list(paths.gitea_config_dir().iterdir()) == []
    verify_mounts(client, paths, "1000:1000", {}, {})
    second_command = client.containers.create.call_args.kwargs["command"]
    assert second_command != command


def test_cancelled_startup_never_creates_resources(tmp_path: Path) -> None:
    from tlon.gitea.control import StartupControl

    control = StartupControl()
    control.stop.set()
    client = MagicMock()
    with pytest.raises(GiteaError, match="cancelled"):
        ensure_container(client, TlonPaths(tmp_path), "example", 3000, control=control)
    client.networks.create.assert_not_called()
    client.containers.create.assert_not_called()


def test_image_pull_stream_deadline_closes_stream(tmp_path: Path) -> None:
    import docker

    from tlon.gitea.control import StartupControl

    paths = TlonPaths(tmp_path)
    paths.gitea_dir().mkdir(parents=True)
    client = MagicMock()
    client.networks.get.side_effect = docker.errors.NotFound("missing")
    client.containers.get.side_effect = docker.errors.NotFound("missing")
    client.images.get.side_effect = docker.errors.ImageNotFound("missing")
    control = StartupControl()
    closed: list[bool] = []

    def progress() -> Iterator[dict[str, str]]:
        try:
            control.deadline = 0
            yield {"status": "Downloading"}
        finally:
            closed.append(True)

    client.api.pull.return_value = progress()
    with pytest.raises(GiteaError, match="timed out"):
        ensure_container(client, paths, "example", 3000, control=control)
    assert closed == [True]
    client.containers.create.assert_not_called()
