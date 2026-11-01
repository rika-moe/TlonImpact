"""Strictly owned Docker resources; never deletes persistent infrastructure."""

import os
import secrets
from typing import Any, cast

import docker

from tlon.base.paths import TlonPaths
from tlon.gitea.control import StartupControl
from tlon.gitea.storage import GiteaError

GITEA_IMAGE = "docker.gitea.com/gitea:28.2.0-rootless"


def verify_mounts(
    client: docker.DockerClient,
    paths: TlonPaths,
    user: str,
    volumes: dict[str, dict[str, str]],
    labels: dict[str, str],
    control: StartupControl | None = None,
) -> None:
    """Prove the daemon sees and can write the actual host directories."""
    control = control or StartupControl()
    control.check()
    nonce = secrets.token_hex(24)
    probe = None
    try:
        for config in (False, True):
            with paths.gitea_mount_probe_file(nonce, config=config).open("w") as marker:
                marker.write(nonce)
                marker.flush()
                os.fsync(marker.fileno())
        control.check()
        probe = client.containers.create(
            GITEA_IMAGE,
            entrypoint=["/bin/sh", "-c"],
            command=[
                f"cat /var/lib/gitea/.tlon-probe-{nonce} > /var/lib/gitea/.tlon-probe-{nonce}-reply && "
                f"cat /etc/gitea/.tlon-probe-{nonce} > /etc/gitea/.tlon-probe-{nonce}-reply"
            ],
            user=user,
            volumes=volumes,
            network_mode="none",
            read_only=True,
            labels=labels,
            detach=True,
        )
        control.check()
        probe.start()
        result = probe.wait(timeout=15)
        if result.get("StatusCode") != 0 or any(
            paths.gitea_mount_probe_file(nonce, config=config, reply=True).read_text() != nonce
            for config in (False, True)
        ):
            raise GiteaError("Gitea host directories are not shared/writable by Docker")
    except OSError as exc:
        raise GiteaError("Gitea host directories are not shared/writable by Docker") from exc
    finally:
        if probe is not None:
            probe.remove(force=True)
        for config in (False, True):
            for reply in (False, True):
                paths.gitea_mount_probe_file(nonce, config=config, reply=reply).unlink(
                    missing_ok=True
                )


def ensure_container(
    client: docker.DockerClient,
    paths: TlonPaths,
    instance_id: str,
    port: int,
    *,
    control: StartupControl | None = None,
) -> Any:
    control = control or StartupControl()
    control.check()
    name = f"tlon-gitea-{instance_id}"
    labels = {
        "io.tlon.gitea.instance": instance_id,
        "io.tlon.gitea.home": str(paths.gitea_dir().resolve()),
    }
    network_name = name + "-network"
    try:
        network = client.networks.get(network_name)
        if network.attrs.get("Labels") != labels or network.attrs.get("Driver") != "bridge":
            raise GiteaError("Gitea network ownership/configuration conflict")
    except docker.errors.NotFound:
        control.check()
        network = client.networks.create(network_name, driver="bridge", labels=labels)
    volumes = {
        str(paths.gitea_data_dir().resolve()): {"bind": "/var/lib/gitea", "mode": "rw"},
        str(paths.gitea_config_dir().resolve()): {"bind": "/etc/gitea", "mode": "rw"},
    }
    user = (
        f"{os.getuid()}:{os.getgid()}"
        if hasattr(os, "getuid") and os.getuid() != 0
        else "1000:1000"
    )
    environment = {
        "USER": "git",
        "GITEA__database__DB_TYPE": "sqlite3",
        "GITEA__security__INSTALL_LOCK": "true",
        "GITEA__service__DISABLE_REGISTRATION": "true",
        "GITEA__service__REQUIRE_SIGNIN_VIEW": "true",
        "GITEA__server__ROOT_URL": f"http://127.0.0.1:{port}/",
        "GITEA__server__HTTP_PORT": "3000",
        "GITEA__server__DISABLE_SSH": "true",
        "GITEA__actions__ENABLED": "false",
    }
    mounts_verified = False
    try:
        container = client.containers.get(name)
    except docker.errors.NotFound:
        for path in (paths.gitea_data_dir(), paths.gitea_config_dir()):
            path.mkdir(mode=0o700, exist_ok=True)
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                os.chown(path, 1000, 1000)
        try:
            client.images.get(GITEA_IMAGE)
        except docker.errors.ImageNotFound:
            control.check()
            stream = client.api.pull(GITEA_IMAGE, stream=True, decode=True)
            try:
                for progress in stream:
                    control.check()
                    if "error" in progress:
                        raise GiteaError("Gitea image pull failed")
            finally:
                stream.close()
            control.check()
        verify_mounts(client, paths, user, volumes, labels, control)
        mounts_verified = True
        control.check()
        container = client.containers.create(
            GITEA_IMAGE,
            name=name,
            labels=labels,
            volumes=volumes,
            ports={"3000/tcp": ("127.0.0.1", port)},
            network=network.name,
            environment=environment,
            user=user,
            detach=True,
            restart_policy=cast(Any, {"Name": "unless-stopped"}),
        )
    attrs = container.attrs
    config = attrs.get("Config", {})
    mounts = {m["Source"]: m["Destination"] for m in attrs.get("Mounts", []) if m.get("RW")}
    expected_mounts = {source: spec["bind"] for source, spec in volumes.items()}
    actual_env = dict(item.split("=", 1) for item in config.get("Env", []) if "=" in item)
    if (
        any(config.get("Labels", {}).get(key) != value for key, value in labels.items())
        or config.get("Image") != GITEA_IMAGE
        or config.get("User") != user
        or mounts != expected_mounts
        or len(attrs.get("Mounts", [])) != 2
        or attrs.get("HostConfig", {}).get("PortBindings")
        != {"3000/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]}
        or any(actual_env.get(key) != value for key, value in environment.items())
        or set(attrs.get("NetworkSettings", {}).get("Networks", {})) != {network_name}
    ):
        raise GiteaError(
            "Gitea container ownership/configuration conflict; explicit recovery required"
        )
    if not mounts_verified:
        verify_mounts(client, paths, user, volumes, labels, control)
    control.check()
    if container.status != "running":
        container.start()
    return container
