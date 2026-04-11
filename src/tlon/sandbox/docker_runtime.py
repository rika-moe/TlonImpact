"""Docker Runtime for Tlon.

Manages Docker containers for agent execution environments.
"""

import asyncio
from dataclasses import dataclass

import docker
from docker.errors import ImageNotFound, NotFound

from tlon.base.config import DockerRuntimeConfig
from tlon.base.logger import get_logger

logger = get_logger(__name__)


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

    Example:
        >>> config = DockerRuntimeConfig(image="ubuntu:22.04")
        >>> runtime = DockerRuntime(config, agent_id="dev-001")
        >>> await runtime.ensure_running()
        >>> result = await runtime.execute("echo Hello")
        >>> print(result.stdout)  # Hello
    """

    def __init__(self, config: DockerRuntimeConfig, agent_id: str):
        """Initialize Docker runtime.

        Args:
            config: Docker runtime configuration
            agent_id: Unique agent identifier (used for container naming)
        """
        self.config = config
        self.agent_id = agent_id
        self.container_name = f"tlon-agent-{agent_id}"
        self.client = docker.from_env()
        self.container: docker.models.containers.Container | None = None
        self._uv_installed = False  # Track uv installation status

    async def ensure_running(self) -> None:
        """Ensure the container is running.

        Creates the container if it doesn't exist, starts it if stopped.
        """
        try:
            # Try to get existing container
            self.container = self.client.containers.get(self.container_name)

            if self.container.status != "running":
                logger.info(f"Starting existing container: {self.container_name}")
                self.container.start()
                # Wait a moment for container to be ready
                await asyncio.sleep(1)

        except NotFound:
            # Container doesn't exist, create it
            logger.info(f"Creating new container: {self.container_name}")
            await self._create_container()

    async def _create_container(self) -> None:
        """Create a new container from the configured image."""
        image = self.config.image

        # Pull image if not exists locally
        try:
            self.client.images.get(image)
            logger.debug(f"Image {image} found locally")
        except ImageNotFound:
            logger.info(f"Pulling image: {image}")
            await asyncio.to_thread(self.client.images.pull, image)
            logger.info(f"Image {image} pulled successfully")

        # Prepare volumes (for persistence)
        volumes = {}

        logger.info(f"Creating container {self.container_name} from image {image}")

        try:
            self.container = self.client.containers.run(
                image=image,
                name=self.container_name,
                command="sleep infinity",  # Keep container running
                working_dir=self.config.working_dir,
                volumes=volumes,
                mem_limit=self.config.memory_limit,
                cpu_period=100000,
                cpu_quota=int(self.config.cpu_limit * 100000),
                detach=True,
                network_mode=self.config.network_mode,  # Full network access
                auto_remove=False,  # Persist container
                stdin_open=True,
                tty=True,
            )

            # Wait for container to be ready
            await asyncio.sleep(2)

            # Initialize container with basic tools
            await self._init_container()

            logger.info(f"Container {self.container_name} created and initialized")

        except Exception as e:
            logger.error(f"Failed to create container: {e}", exc_info=True)
            raise

    async def _init_container(self) -> None:
        """Initialize container with basic tools."""
        init_commands = [
            "apt-get update -qq",
            "apt-get install -y -qq curl wget git vim nano python3 python3-pip",
        ]

        for cmd in init_commands:
            logger.debug(f"Running init command: {cmd}")
            result = await self.execute(cmd, timeout=120)
            if result.exit_code != 0:
                logger.warning(f"Init command failed: {cmd}\n{result.stderr}")
                # Continue even if some commands fail

        # Install uv for PEP 723 script support
        await self._install_uv()

    async def _install_uv(self) -> None:
        """Install uv package manager for PEP 723 script support.

        uv allows running Python scripts with inline dependency declarations
        without requiring a separate virtual environment or requirements.txt.

        See: https://docs.astral.sh/uv/
        """
        if self._uv_installed:
            return

        logger.info("Installing uv package manager...")

        # Install uv using the official installer
        install_cmd = "curl -LsSf https://astral.sh/uv/install.sh | sh"
        result = await self.execute(install_cmd, timeout=60)

        if result.exit_code == 0:
            # Add uv to PATH for the container
            # The installer puts uv in ~/.local/bin
            path_cmd = 'export PATH="$HOME/.local/bin:$PATH" && uv --version'
            result = await self.execute(path_cmd, timeout=10)

            if result.exit_code == 0:
                self._uv_installed = True
                logger.info(f"uv installed successfully: {result.stdout.strip()}")
            else:
                logger.warning(f"uv installed but version check failed: {result.stderr}")
        else:
            logger.warning(f"Failed to install uv: {result.stderr}")

    def is_uv_installed(self) -> bool:
        """Check if uv is installed in the container.

        Returns:
            True if uv is available
        """
        return self._uv_installed

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
            # Run command in container
            exec_result = self.container.exec_run(
                cmd=["bash", "-c", command],
                workdir=workdir or self.config.working_dir,
                demux=True,  # Separate stdout/stderr
            )

            # Wait for completion with timeout
            try:
                exit_code, output = await asyncio.wait_for(
                    asyncio.to_thread(lambda: (exec_result.exit_code, exec_result.output)),
                    timeout=timeout,
                )
            except TimeoutError:
                # Try to kill the exec if possible
                logger.warning(f"Command timed out after {timeout}s: {command[:100]}")
                return ExecutionResult(
                    exit_code=-1, stdout="", stderr=f"Command timed out after {timeout} seconds"
                )

            # Parse output (demux returns tuple of (stdout_bytes, stderr_bytes))
            stdout_bytes, stderr_bytes = output if output else (b"", b"")
            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

            return ExecutionResult(exit_code=exit_code, stdout=stdout, stderr=stderr)

        except Exception as e:
            logger.error(f"Error executing command: {e}", exc_info=True)
            return ExecutionResult(exit_code=-1, stdout="", stderr=str(e))

    async def stop(self) -> None:
        """Stop the container."""
        if self.container:
            try:
                logger.info(f"Stopping container: {self.container_name}")
                await asyncio.to_thread(self.container.stop, timeout=10)
            except Exception as e:
                logger.warning(f"Error stopping container: {e}")

    async def remove(self, force: bool = False) -> None:
        """Remove the container.

        Args:
            force: Force removal even if running
        """
        if self.container:
            try:
                logger.info(f"Removing container: {self.container_name}")
                await asyncio.to_thread(self.container.remove, force=force)
                self.container = None
            except Exception as e:
                logger.warning(f"Error removing container: {e}")

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
            logger.error(f"Error getting logs: {e}")
            return ""
