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

import docker
from docker.errors import ImageNotFound, NotFound
from docker.models.containers import Container

from tlon.base.config import DockerRuntimeConfig
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths

logger = get_logger(__name__)

#: Container path where the agent's own home directory (``~/.tlon/{agent_id}``)
#: is bind-mounted read-only (issue ): readFile/listDirectory/searchFiles
#: whitelist it (agents/assembly.py), writeFile/editFile do not, and the ``ro``
#: mount is the kernel-level guarantee. Only the agent's own home is mounted —
#: other members' homes never enter the container, so cross-agent reads are
#: structurally impossible.
AGENT_HOME_CONTAINER_PATH = "/tlon-home"


@dataclass
class ExecutionResult:
    """Result of a command execution."""

    exit_code: int
    stdout: str
    stderr: str


class DockerRuntime:
    """Docker runtime for agent command execution.

    Each agent gets its own isolated container with full network access.
    Containers are created on-demand and persist across sessions.

    Lifecycle (unified member model, ADR 0006): containers are never
    force-removed — every member's container is kept across sleeps and
    server restarts (waking is zero-cost; the next boot lazily reattaches
    by name). Members are never discarded; they only sleep.

    File I/O is performed via 'cat' (reads) and put_archive with an
    in-memory tar (writes), so no software needs to be installed inside
    the container.

    Example:
        >>> config = DockerRuntimeConfig(image="ubuntu:22.04")
        >>> runtime = DockerRuntime(config, agent_id="dev-001", tlon_paths=paths)
        >>> await runtime.ensure_running()
        >>> result = await runtime.execute("echo Hello")
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

    def _create_client(self) -> docker.DockerClient:
        """Create a Docker client, trying multiple connection methods.

        Supports:
        - DOCKER_HOST env var (Colima, Docker Desktop, custom setups)
        - Default Unix socket (/var/run/docker.sock)
        - macOS Colima socket (~/.colima/default/docker.sock)
        - macOS Docker Desktop socket (~/.docker/run/docker.sock)
        """
        # First try: environment variables (honors DOCKER_HOST set by Colima/Docker Desktop)
        try:
            return docker.from_env()
        except Exception:
            pass

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
                except Exception:
                    continue

        # Last resort: let from_env raise the original error
        return docker.from_env()

    async def ensure_running(self) -> None:
        """Ensure the container is running.

        Creates the container if it doesn't exist, starts it if stopped.
        """
        try:
            # Try to get existing container (blocking docker SDK call → thread)
            container = await asyncio.to_thread(self.client.containers.get, self.container_name)
            self.container = container

            if container.status != "running":
                logger.info("container_start", container_name=self.container_name)
                await asyncio.to_thread(container.start)
                # Wait a moment for container to be ready
                await asyncio.sleep(1)

        except NotFound:
            # Container doesn't exist, create it
            logger.info("container_creation", container_name=self.container_name)
            await self._create_container()

    async def _create_container(self) -> None:
        """Create a new container from the configured image.

        Image is pulled if not cached locally. No software is installed —
        the image is used as-is.
        """
        image = self.config.image

        try:
            await asyncio.to_thread(self.client.images.get, image)
            logger.debug("image_found", image=image)
        except ImageNotFound:
            logger.info("image_pull", image=image)
            await asyncio.to_thread(self.client.images.pull, image)
            logger.info("image_pulled", image=image)

        workspace = self._paths.agent_workspace_dir(self.agent_id)
        # Own home read-only (issue ): the agent can inspect its behavior
        # spec, memory and mailbox through the filesystem tools; the ro mount
        # keeps writes impossible even if a whitelist were misconfigured.
        agent_home = self._paths.agent_dir(self.agent_id)
        volumes = {
            str(workspace): {
                "bind": self.config.working_dir,
                "mode": "rw",
            },
            str(agent_home): {
                "bind": AGENT_HOME_CONTAINER_PATH,
                "mode": "ro",
            },
        }

        logger.info("container_run", container_name=self.container_name, image=image)

        self.container = await asyncio.to_thread(
            self.client.containers.run,
            image=image,
            name=self.container_name,
            command="sleep infinity",
            working_dir=self.config.working_dir,
            volumes=volumes,
            mem_limit=self.config.memory_limit,
            cpu_period=100000,
            cpu_quota=int(self.config.cpu_limit * 100000),
            detach=True,
            network_mode=self.config.network_mode,
            auto_remove=False,
            stdin_open=True,
            tty=True,
        )

        logger.info("container_created", container_name=self.container_name)

    async def execute(
        self, command: str, timeout: int = 60, workdir: str | None = None
    ) -> ExecutionResult:
        """Execute a command in the container.

        Args:
            command: The command to execute
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
                        cmd=["timeout", str(timeout), "bash", "-c", command],
                        workdir=workdir or self.config.working_dir,
                        demux=True,  # Separate stdout/stderr
                    ),
                    timeout=timeout + 5,
                )
            except TimeoutError:
                logger.warning("command_timeout", timeout=timeout, command=command)
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
                logger.warning("command_timeout", timeout=timeout, command=command)
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

        Uses 'cat' inside the container — symlinks are followed automatically.

        Args:
            path: Absolute path inside the container.

        Returns:
            Raw file contents as bytes.

        Raises:
            FileNotFoundError: If the file does not exist in the container.
            RuntimeError: If the read operation fails.
        """
        if not self.container:
            await self.ensure_running()

        logger.debug("file_read", path=path)

        result = await self.execute(f"cat {shlex.quote(path)}")

        if result.exit_code != 0:
            if "No such file" in result.stderr or "not found" in result.stderr.lower():
                raise FileNotFoundError(f"File not found in container: {path}")
            logger.error("file_read_error", path=path, stderr=result.stderr)
            raise RuntimeError(f"cat failed: {result.stderr}")

        return result.stdout.encode("utf-8")

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
            result = await self.execute(f"mkdir -p {shlex.quote(parent)}")
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

        result = await self.execute(f"test -e {shlex.quote(path)} && echo EXISTS || echo MISSING")
        return "EXISTS" in result.stdout
