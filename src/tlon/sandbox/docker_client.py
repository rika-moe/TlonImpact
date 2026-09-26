"""Shared Docker client probing for the sandbox layer.

Single source of truth for connecting to the Docker daemon, used by both
``DockerRuntime`` (container lifecycle) and ``ContainerResourceMonitor``
(resource ledger, issue #24). Extraction keeps the probe chain — env vars,
then well-known socket paths — identical for every docker consumer, so a
daemon connection problem is diagnosed in exactly one place.
"""

from pathlib import Path

import docker

from tlon.base.logger import get_logger

logger = get_logger(__name__)


def create_docker_client() -> docker.DockerClient:
    """Create a Docker client, trying multiple connection methods.

    Supports:
    - DOCKER_HOST env var (Colima, Docker Desktop, custom setups)
    - Default Unix socket (/var/run/docker.sock)
    - macOS Colima socket (~/.colima/default/docker.sock)
    - macOS Docker Desktop socket (~/.docker/run/docker.sock)

    Each probe failure is logged at debug level so a daemon connection
    problem is diagnosable before the final raise.
    """
    # First try: environment variables (honors DOCKER_HOST set by Colima/Docker Desktop)
    try:
        return docker.from_env()
    except Exception as e:
        logger.debug("docker_client_from_env_failed", error=str(e))

    # Second try: common socket paths on macOS
    socket_paths = [
        Path.home() / ".colima" / "default" / "docker.sock",
        Path.home() / ".docker" / "run" / "docker.sock",
        Path("/var/run/docker.sock"),
        Path.home() / ".colima" / "docker.sock",
    ]

    for sock_path in socket_paths:
        if sock_path.exists():
            try:
                return docker.DockerClient(
                    base_url=f"unix://{sock_path}",
                    timeout=60,
                )
            except Exception as e:
                logger.debug("docker_client_socket_failed", socket=str(sock_path), error=str(e))
                continue

    # Last resort: let from_env raise the original error
    return docker.from_env()
