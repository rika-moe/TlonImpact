"""Docker Runtime for Tlon.

Manages Docker containers for agent execution environments.
Provides both command execution and file I/O APIs.

The container is created from the configured image as-is — no software
installation is performed at runtime. Tools (python3, curl, git, etc.)
must be present in the base image or installed via a Dockerfile.
"""

import asyncio
import io
import shlex
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import docker
from docker.errors import APIError, ImageNotFound, NotFound
from docker.models.containers import Container
from docker.utils import parse_bytes

from tlon.base.config import DockerRuntimeConfig
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths
from tlon.sandbox.device import ExecutionResult
from tlon.sandbox.docker_client import create_docker_client

logger = get_logger(__name__)

#: Container path where the agent's own home directory (``~/.tlon/{agent_id}``)
#: is bind-mounted read-only (issue ): readFile/listDirectory/searchFiles
#: whitelist it (agents/assembly.py), writeFile/editFile do not, and the ``ro``
#: mount is the kernel-level guarantee. Only the agent's own home is mounted —
#: other members' homes never enter the container, so cross-agent reads are
#: structurally impossible.
AGENT_HOME_CONTAINER_PATH = "/tlon-home"

#: Scratch space inside the container: with a read-only rootfs most programs
#: still need a writable /tmp. tmpfs is RAM-backed and dies with the container.
TMPFS_MOUNTS: dict[str, str] = {"/tmp": "rw,nosuid,nodev,size=64m"}

#: cpu_period (microseconds) paired with cpu_quota at container creation.
CPU_PERIOD = 100000


@dataclass(frozen=True)
class _ContainerSpec:
    """Expected container configuration, mirroring ``_create_container``'s parameters.

    ``binds`` are in docker inspect form (``"host:container:mode"``).
    """

    image: str
    binds: frozenset[str]
    memory_bytes: int
    cpu_period: int
    cpu_quota: int
    network_mode: str
    read_only: bool
    tmpfs: dict[str, str]


def _container_mismatches(attrs: dict[str, Any], expected: _ContainerSpec) -> list[str]:
    """Return the names of the dimensions where actual config differs from expected.

    Pure function over the docker inspect payload (``container.attrs``) so it
    is unit-testable without a daemon. Field sources:

    - image: ``Config.Image`` — the reference passed at creation time, immune
      to later tag re-resolution (unlike ``container.image.tags``)
    - binds / mem_limit / cpu / network_mode / read_only / tmpfs:
      ``HostConfig.Binds`` / ``.Memory`` / ``.CpuPeriod``+``.CpuQuota`` /
      ``.NetworkMode`` / ``.ReadonlyRootfs`` / ``.Tmpfs``

    Missing keys (containers created before a dimension existed) count as
    mismatch via their zero value.
    """
    config = attrs.get("Config") or {}
    host_config = attrs.get("HostConfig") or {}
    mismatches: list[str] = []
    if config.get("Image") != expected.image:
        mismatches.append("image")
    if set(host_config.get("Binds") or []) != expected.binds:
        mismatches.append("binds")
    if (host_config.get("Memory") or 0) != expected.memory_bytes:
        mismatches.append("mem_limit")
    if (host_config.get("CpuPeriod") or 0) != expected.cpu_period or (
        host_config.get("CpuQuota") or 0
    ) != expected.cpu_quota:
        mismatches.append("cpu")
    if host_config.get("NetworkMode") != expected.network_mode:
        mismatches.append("network_mode")
    if bool(host_config.get("ReadonlyRootfs")) != expected.read_only:
        mismatches.append("read_only")
    if (host_config.get("Tmpfs") or {}) != expected.tmpfs:
        mismatches.append("tmpfs")
    return mismatches


def _container_matches(attrs: dict[str, Any], expected: _ContainerSpec) -> bool:
    """True if the container's actual config matches the expected spec on every dimension."""
    return not _container_mismatches(attrs, expected)


class DockerRuntime:
    """Docker runtime for agent command execution.

    First implementation of the ``ExecutionDevice`` interface
    (``sandbox/device.py``, design: docs/design/execution-device.md).

    Each agent gets its own isolated container. Network is off by default
    (``network_mode="none"``, issue #23 — constitution zero: mail is the
    only channel); a member that genuinely needs egress declares it
    explicitly in its agent.yaml. Containers are created on-demand and
    persist across sessions.

    Lifecycle (unified member model, ADR 0006): containers are kept across
    sleeps and server restarts (waking is zero-cost; the next boot lazily
    reattaches by name). Members are never discarded; they only sleep.
    The one exception is configuration drift: ``ensure_running`` compares
    the existing container's actual config against the expected spec
    (image, mounts, limits, network, rootfs hardening) and rebuilds it on
    mismatch — workspace state lives on the bind mount, so nothing is
    lost. Containers created before a hardening/config change are rebuilt
    exactly once on their next wake; that one-time rebuild is how new
    hardening reaches existing containers.

    File I/O is performed via the docker SDK's get_archive (reads) and
    put_archive (writes) with in-memory tar archives, so no software needs
    to be installed inside the container.

    Example:
        >>> config = DockerRuntimeConfig(image="ubuntu:22.04")
        >>> runtime = DockerRuntime(config, agent_id="dev-001", tlon_paths=paths)
        >>> await runtime.ensure_running()
        >>> result = await runtime.run("echo Hello")
        >>> print(result.stdout)  # Hello
    """

    def __init__(self, config: DockerRuntimeConfig, agent_id: str, tlon_paths: TlonPaths):
        """Initialize Docker runtime.

        Args:
            config: Docker runtime configuration
            agent_id: Unique agent identifier (used for container naming)
            tlon_paths: Path resolver (workspace + home bind-mount sources)
        """
        self.config = config
        self.agent_id = agent_id
        self._paths = tlon_paths
        self.container_name = f"tlon-agent-{agent_id}"
        self.client = self._create_client()
        self.container: Container | None = None
        self._closed = False

    def _create_client(self) -> docker.DockerClient:
        """Create a Docker client via the shared sandbox probe chain
        (``sandbox/docker_client.create_docker_client``).

        Kept as a method so tests can monkeypatch the creation point on
        the class; behavior is identical to calling the shared function.
        """
        return create_docker_client()

    def close(self) -> None:
        """Close the underlying Docker client (its requests session and
        connection pool). Idempotent — repeated calls are no-ops.

        Ownership: whoever assembles the runtime closes it — assembly
        (``agents/assembly.py``) creates one ``DockerRuntime`` per member
        wake and stashes it on ``CognitiveCapabilities.docker_runtime``;
        ``MemberRuntime.shutdown`` closes it. The container is unaffected:
        per ADR 0006 a member's container survives sleep (removed only on
        config drift — see ``ensure_running``), so the sleeping member
        re-attaches by name on the next wake. After close() the instance is semantically retired — tools holding a
        reference must not be called again.
        """
        if self._closed:
            return
        self._closed = True
        self.client.close()

    async def ensure_running(self) -> None:
        """Ensure the container is running.

        Creates the container if it doesn't exist, starts it if stopped.
        If an existing container's actual config has drifted from the
        expected spec (``_expected_spec`` / ``_container_mismatches``), it
        is removed and recreated — workspace state survives on the bind
        mount, so the rebuild is transparent to the member.
        """
        try:
            # Try to get existing container (blocking docker SDK call → thread)
            container = await asyncio.to_thread(self.client.containers.get, self.container_name)
        except NotFound:
            # Container doesn't exist, create it
            logger.info("container_creation", container_name=self.container_name)
            await self._create_container()
            return

        mismatches = _container_mismatches(container.attrs, self._expected_spec())
        if mismatches:
            logger.info(
                "container_config_drift",
                container_name=self.container_name,
                dimensions=mismatches,
            )
            await asyncio.to_thread(container.remove, force=True)
            self.container = None
            await self._create_container()
            return

        self.container = container
        if container.status != "running":
            logger.info("container_start", container_name=self.container_name)
            await asyncio.to_thread(container.start)
            # Wait a moment for container to be ready
            await asyncio.sleep(1)

    def _expected_spec(self) -> _ContainerSpec:
        """Build the expected container spec from config + paths.

        Kept in lockstep with ``_create_container``'s parameters — the drift
        reconcile in ``ensure_running`` compares against this.
        """
        binds = frozenset(
            f"{host}:{mount['bind']}:{mount['mode']}" for host, mount in self._bind_mounts().items()
        )
        return _ContainerSpec(
            image=self.config.image,
            binds=binds,
            memory_bytes=int(parse_bytes(self.config.memory_limit)),
            cpu_period=CPU_PERIOD,
            cpu_quota=int(self.config.cpu_limit * CPU_PERIOD),
            network_mode=self.config.network_mode,
            read_only=True,
            tmpfs=dict(TMPFS_MOUNTS),
        )

    def _bind_mounts(self) -> dict[str, dict[str, str]]:
        """Bind-mount declaration in docker SDK ``volumes`` form (single source
        of truth for both container creation and drift comparison)."""
        workspace = self._paths.agent_workspace_dir(self.agent_id)
        # Own home read-only (issue ): the agent can inspect its behavior
        # spec, memory and mailbox through the filesystem tools; the ro mount
        # keeps writes impossible even if a whitelist were misconfigured.
        agent_home = self._paths.agent_dir(self.agent_id)
        return {
            str(workspace): {
                "bind": self.config.working_dir,
                "mode": "rw",
            },
            str(agent_home): {
                "bind": AGENT_HOME_CONTAINER_PATH,
                "mode": "ro",
            },
        }

    async def _create_container(self) -> None:
        """Create a new container from the configured image.

        Image is pulled if not cached locally. No software is installed —
        the image is used as-is.

        The rootfs is mounted read-only (``read_only=True``) with a small
        tmpfs at ``/tmp`` for scratch (many programs need a writable /tmp);
        the only persistent writable surface is the ``/workspace`` bind
        mount — ``/tlon-home`` stays read-only, so programs that need to
        persist anything must write under ``/workspace``. This makes
        "writable surface = workspace" a kernel-level fact and closes the
        write-side symlink escape: an agent can plant a symlink in
        ``/workspace`` pointing at e.g. ``/etc/passwd`` via bash, but
        ``put_archive`` does not follow it — daemon-side tar extraction
        *replaces* the symlink with a regular file, leaving the target
        untouched (verified by live-daemon smoke: ``/etc/passwd`` md5
        unchanged, ``/workspace/evil-link`` becomes a regular file).
        Defense chain: path_guard (string level) → ro rootfs (kernel
        level). The kernel-level half cannot be unit-tested (the suite
        has no real Docker daemon); it is verified by smoke test against
        a live daemon. Read-side symlink resolution is unaffected and by
        design (the container filesystem is readable; see ``read_file``).
        """
        image = self.config.image

        try:
            await asyncio.to_thread(self.client.images.get, image)
            logger.debug("image_found", image=image)
        except ImageNotFound:
            logger.info("image_pull", image=image)
            await asyncio.to_thread(self.client.images.pull, image)
            logger.info("image_pulled", image=image)

        volumes = self._bind_mounts()
        for host_dir in volumes:
            Path(host_dir).mkdir(parents=True, exist_ok=True)

        logger.info("container_run", container_name=self.container_name, image=image)

        self.container = await asyncio.to_thread(
            self.client.containers.run,
            image=image,
            name=self.container_name,
            command="sleep infinity",
            working_dir=self.config.working_dir,
            volumes=volumes,
            mem_limit=self.config.memory_limit,
            cpu_period=CPU_PERIOD,
            cpu_quota=int(self.config.cpu_limit * CPU_PERIOD),
            detach=True,
            network_mode=self.config.network_mode,
            auto_remove=False,
            stdin_open=True,
            tty=True,
            read_only=True,
            tmpfs=dict(TMPFS_MOUNTS),
        )

        logger.info("container_created", container_name=self.container_name)

    async def run(
        self, program: str, timeout: int = 60, workdir: str | None = None
    ) -> ExecutionResult:
        """Run a program (shell command) in the container.

        Implements ``ExecutionDevice.run``.

        Args:
            program: The command to execute
            timeout: Maximum execution time in seconds
            workdir: Optional working directory override

        Returns:
            ExecutionResult with exit code and output
        """
        if not self.container:
            await self.ensure_running()

        if not self.container:
            return ExecutionResult(exit_code=-1, stdout="", stderr="Container not available")

        try:
            # exec_run is a blocking docker SDK call — run it in a thread so
            # the event loop stays responsive, and enforce the timeout in two
            # layers: `timeout` inside the container actually kills the
            # process (a timed-out thread cannot be cancelled), while
            # asyncio.wait_for (with a grace period) is the outer backstop.
            try:
                exec_result = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.container.exec_run,
                        cmd=["timeout", str(timeout), "bash", "-c", program],
                        workdir=workdir or self.config.working_dir,
                        demux=True,  # Separate stdout/stderr
                    ),
                    timeout=timeout + 5,
                )
            except TimeoutError:
                logger.warning("command_timeout", timeout=timeout, command=program)
                return ExecutionResult(
                    exit_code=-1, stdout="", stderr=f"Command timed out after {timeout} seconds"
                )

            exit_code: int = exec_result.exit_code if exec_result.exit_code is not None else -1
            output = exec_result.output

            # Parse output (demux returns tuple of (stdout_bytes, stderr_bytes))
            stdout_bytes, stderr_bytes = output if output else (b"", b"")
            stdout = (
                stdout_bytes.decode("utf-8", errors="replace")
                if isinstance(stdout_bytes, bytes)
                else ""
            )
            stderr = (
                stderr_bytes.decode("utf-8", errors="replace")
                if isinstance(stderr_bytes, bytes)
                else ""
            )

            # `timeout` kills the command with exit code 124
            if exit_code == 124:
                logger.warning("command_timeout", timeout=timeout, command=program)
                return ExecutionResult(
                    exit_code=-1,
                    stdout=stdout,
                    stderr=f"Command timed out after {timeout} seconds",
                )

            return ExecutionResult(exit_code=exit_code, stdout=stdout, stderr=stderr)

        except Exception as e:
            logger.error("command_execution_error", error=str(e), exc_info=True)
            return ExecutionResult(exit_code=-1, stdout="", stderr=str(e))

    async def stop(self) -> None:
        """Stop the container."""
        if self.container:
            try:
                logger.info("container_stop", container_name=self.container_name)
                await asyncio.to_thread(self.container.stop, timeout=10)
            except Exception as e:
                logger.warning("container_stop_error", error=str(e))

    async def remove(self, force: bool = False) -> None:
        """Remove the container.

        Args:
            force: Force removal even if running
        """
        if self.container:
            try:
                logger.info("container_removal", container_name=self.container_name)
                await asyncio.to_thread(self.container.remove, force=force)
                self.container = None
            except Exception as e:
                logger.warning("container_removal_error", error=str(e))

    def is_running(self) -> bool:
        """Check if container is running.

        Returns:
            True if container exists and is running
        """
        if not self.container:
            return False
        try:
            self.container.reload()
            return self.container.status == "running"
        except Exception:
            return False

    async def get_logs(self, tail: int = 100) -> str:
        """Get container logs.

        Args:
            tail: Number of lines to return

        Returns:
            Container logs as string
        """
        if not self.container:
            return ""

        try:
            logs = await asyncio.to_thread(
                self.container.logs, tail=tail, timestamps=True, stdout=True, stderr=True
            )
            return logs.decode("utf-8", errors="replace")
        except Exception as e:
            logger.error("container_logs_error", error=str(e))
            return ""

    # ------------------------------------------------------------------
    # File I/O APIs (sandbox-only — Agent never touches host filesystem)
    # ------------------------------------------------------------------

    async def read_file(self, path: str) -> bytes:
        """Read a file from the container.

        Uses the docker SDK's get_archive (symmetric with write_file's
        put_archive), so no software (cat, python3, ...) needs to exist
        inside the container and arbitrary bytes round-trip losslessly —
        the payload never passes through text decoding. The daemon follows
        symlinks server-side (stat reports ``linkTarget``), matching the
        old 'cat' behavior.

        Args:
            path: Absolute path inside the container.

        Returns:
            Raw file contents as bytes.

        Raises:
            FileNotFoundError: If the file does not exist in the container.
            RuntimeError: If the path is a directory / not a regular file,
                or the read operation fails.
        """
        if not self.container:
            await self.ensure_running()

        if not self.container:
            raise RuntimeError("Container not available")

        logger.debug("file_read", path=path)

        try:
            tar_bytes = await asyncio.to_thread(self._fetch_archive, path)
        except NotFound as e:
            raise FileNotFoundError(f"File not found in container: {path}") from e
        except APIError as e:
            logger.error("file_read_error", path=path, error=str(e))
            raise RuntimeError(f"get_archive failed for {path}: {e}") from e

        filename = path.rsplit("/", 1)[-1]
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
            member = next(
                (m for m in tar.getmembers() if m.name.removeprefix("./") == filename),
                None,
            )
            if member is None or not member.isfile():
                raise RuntimeError(f"Path is a directory or not a regular file: {path}")
            extracted = tar.extractfile(member)
            if extracted is None:
                raise RuntimeError(f"Failed to extract {filename} from archive for {path}")
            return extracted.read()

    def _fetch_archive(self, path: str) -> bytes:
        """Fetch a path from the container as tar bytes (blocking — run in a thread).

        get_archive returns a lazy chunk generator; it must be fully consumed
        inside the same thread that opened it, so collection happens here.

        Args:
            path: Absolute path inside the container.

        Returns:
            The complete tar archive as bytes.

        Raises:
            docker.errors.NotFound: If the path does not exist.
            docker.errors.APIError: On other daemon errors.
        """
        if self.container is None:
            raise RuntimeError("Container not available")
        stream, _stat = self.container.get_archive(path)
        buf = io.BytesIO()
        for chunk in stream:
            buf.write(chunk)
        return buf.getvalue()

    async def write_file(self, path: str, content: bytes) -> bool:
        """Write a file into the container.

        Uses the docker SDK's put_archive with an in-memory tar archive, so
        no software (python3, base64, ...) needs to exist inside the container.
        Automatically creates parent directories.

        Args:
            path: Absolute path inside the container where the file will be written.
            content: Raw file contents as bytes.

        Returns:
            True if the file was written successfully.

        Raises:
            RuntimeError: If the write operation fails.
        """
        if not self.container:
            await self.ensure_running()

        if not self.container:
            raise RuntimeError("Container not available")

        # Ensure parent directories exist
        parent = path.rsplit("/", 1)[0] if "/" in path else "/"
        if parent and parent != "/":
            result = await self.run(f"mkdir -p {shlex.quote(parent)}")
            if result.exit_code != 0:
                raise RuntimeError(f"mkdir -p failed for {parent}: {result.stderr}")

        # Build an in-memory tar archive containing the single file
        filename = path.rsplit("/", 1)[-1]
        tar_stream = io.BytesIO()
        info = tarfile.TarInfo(name=filename)
        info.size = len(content)
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            tar.addfile(info, io.BytesIO(content))

        ok = await asyncio.to_thread(
            self.container.put_archive, parent or "/", tar_stream.getvalue()
        )
        if not ok:
            logger.error("file_write_error", path=path)
            raise RuntimeError(f"write_file failed: put_archive returned False for {path}")

        logger.debug("file_written", path=path, size=len(content))
        return True

    async def file_exists(self, path: str) -> bool:
        """Check if a path exists in the container.

        Args:
            path: Absolute path inside the container.

        Returns:
            True if the path exists.
        """
        if not self.container:
            await self.ensure_running()

        result = await self.run(f"test -e {shlex.quote(path)} && echo EXISTS || echo MISSING")
        return "EXISTS" in result.stdout
