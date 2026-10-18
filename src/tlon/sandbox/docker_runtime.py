"""Docker Runtime for Tlon.

Manages Docker containers for agent execution environments.
Provides both command execution and file I/O APIs.

Execution model (issue #25, design: docs/design/execution-device.md,
混合方案 D' — constitution three: experiments must be cheap, failure
must be boring):

- **File cabinet (sterile)**: one persistent per-member container that
  serves file I/O only (get_archive/put_archive work on created-but-
  never-started containers — verified against a live daemon). It never
  executes anything, so it can never be polluted.
- **One-shot execution units**: ``run()`` never execs into the cabinet;
  every command gets a fresh container (same image/mounts/read-only
  rootfs/tmpfs/limits/network via the shared ``_container_kwargs``),
  which is removed after its output is collected. Process leaks, /tmp
  accumulation and environment pollution evaporate with the container.
  The only persistent state is ``/workspace`` (bind mount) — the
  explicit persistence boundary.

The container is created from the configured image as-is — no software
installation is performed at runtime. Tools (python3, curl, git, etc.)
must be present in the base image or installed via a Dockerfile.
"""

import asyncio
import io
import tarfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

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

_T = TypeVar("_T")

#: Container path where the agent's own home directory (``~/.tlon/{agent_id}``)
#: is bind-mounted read-only (see docs/design/execution-device.md 「纵深防御」):
#: readFile/listDirectory/searchFiles
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

#: Name segment separating an ephemeral exec unit's owning agent_id from
#: its uuid suffix (``tlon-agent-{agent_id}-exec-{uuid8}``); the resource
#: monitor (resource_monitor.py) uses it to attribute exec units to
#: their owning member instead of spawning per-command metric series.
EXEC_NAME_MARKER = "-exec-"


@dataclass(frozen=True)
class _ContainerSpec:
    """Expected container configuration, mirroring ``_container_kwargs``.

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

    Each agent gets its own isolated execution environment. Network is
    off by default (``network_mode="none"``, issue #23 — constitution
    zero: mail is the only channel); a member that genuinely needs
    egress declares it explicitly in its agent.yaml.

    Two container roles (issue #25):

    - **File cabinet** (``self.container_name``): one persistent
      container per member, created but **never started** — archive
      operations (get_archive/put_archive) work on stopped containers,
      so file I/O needs no running process and the cabinet stays
      sterile. It is kept across sleeps and server restarts (waking is
      zero-cost; the next boot lazily reattaches by name, ADR 0006).
      The one exception is configuration drift: ``ensure_running``
      compares the cabinet's actual config against the expected spec
      (image, mounts, limits, network, rootfs hardening) and rebuilds
      it on mismatch — workspace state lives on the bind mount, so
      nothing is lost. Cabinets created before a hardening/config
      change are rebuilt exactly once on their next wake.
    - **Execution units**: ``run()`` creates a fresh one-shot container
      per command (``tlon-agent-{agent_id}-exec-{uuid8}``) with the
      exact same image/mounts/limits/hardening as the cabinet (shared
      ``_container_kwargs``), waits for it, collects stdout/stderr,
      then removes it. Nothing survives a command except what it wrote
      under ``/workspace``.

    File I/O is performed via the docker SDK's get_archive (reads) and
    put_archive (writes) with in-memory tar archives, so no software
    needs to be installed inside the container.

    Example:
        >>> config = DockerRuntimeConfig(image="ubuntu:22.04")
        >>> runtime = DockerRuntime(config, agent_id="dev-001", tlon_paths=paths)
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
        wake and stashes it on ``MemberCapabilities.docker_runtime``;
        ``MemberRuntime.shutdown`` closes it. The cabinet container is
        unaffected: per ADR 0006 a member's container survives sleep
        (removed only on config drift — see ``ensure_running``), so the
        sleeping member re-attaches by name on the next wake. After
        close() the instance is semantically retired — tools holding a
        reference must not be called again.
        """
        if self._closed:
            return
        self._closed = True
        self.client.close()

    async def ensure_running(self) -> None:
        """Ensure the file cabinet exists and is usable for file I/O.

        Creates the cabinet if it doesn't exist. The cabinet is **never
        started**: get_archive/put_archive work on created-not-started
        containers (verified against a live daemon, issue #25), and a
        container that never runs can never be polluted. The name is
        kept from the pet-container era — "running" now means "ready
        for I/O".

        If an existing cabinet's actual config has drifted from the
        expected spec (``_expected_spec`` / ``_container_mismatches``),
        it is removed and recreated — workspace state survives on the
        bind mount, so the rebuild is transparent to the member.
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

    def _expected_spec(self) -> _ContainerSpec:
        """Build the expected container spec from config + paths.

        Kept in lockstep with ``_container_kwargs`` — the drift
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
        # Own home read-only (see docs/design/execution-device.md 「纵深防御」):
        # the agent can inspect its behavior
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

    def _container_kwargs(
        self, *, name: str, command: list[str], working_dir: str | None = None
    ) -> dict[str, Any]:
        """Container creation parameters — single source for the file
        cabinet and every ephemeral execution unit.

        Both roles share image / bind mounts / memory+cpu limits /
        network / read-only rootfs / tmpfs hardening, so an execution
        unit can never drift from the cabinet's security posture. Kept
        in lockstep with ``_expected_spec`` — the drift reconcile in
        ``ensure_running`` compares against that. Bind-mount sources are
        mkdir'd here so exec units work even before the cabinet exists.
        """
        volumes = self._bind_mounts()
        for host_dir in volumes:
            Path(host_dir).mkdir(parents=True, exist_ok=True)
        return {
            "image": self.config.image,
            "name": name,
            "command": command,
            "working_dir": working_dir or self.config.working_dir,
            "volumes": volumes,
            "mem_limit": self.config.memory_limit,
            "cpu_period": CPU_PERIOD,
            "cpu_quota": int(self.config.cpu_limit * CPU_PERIOD),
            "network_mode": self.config.network_mode,
            "read_only": True,
            "tmpfs": dict(TMPFS_MOUNTS),
        }

    async def _create_container(self) -> None:
        """Create the file cabinet container (never started).

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

        logger.info("container_run", container_name=self.container_name, image=image)

        # Created but never started: archive operations work on stopped
        # containers, and a cabinet that never runs stays sterile.
        self.container = await asyncio.to_thread(
            self.client.containers.create,
            **self._container_kwargs(name=self.container_name, command=["sleep", "infinity"]),
        )

        logger.info("container_created", container_name=self.container_name)

    async def run(
        self, program: str, timeout: int = 60, workdir: str | None = None
    ) -> ExecutionResult:
        """Run a program (shell command) in a one-shot execution unit.

        Implements ``ExecutionDevice.run``. Every call creates a fresh
        container (``tlon-agent-{agent_id}-exec-{uuid8}``) with the same
        image/mounts/limits/hardening as the file cabinet, starts it,
        waits for completion, collects stdout/stderr, and removes it in
        a ``finally`` (``remove(force=True)`` also covers the error and
        outer-timeout paths — no auto_remove, which races with logs()).
        The cabinet is not touched and never executes anything.

        Level gate (issue #40): code execution exists only at L2. At
        other declared levels ``run`` raises immediately — this is the
        device-layer backstop; the normal path never reaches it because
        assembly does not register the bash tools for non-L2 members.

        Timeout is enforced in two layers: the container's own
        ``timeout`` command actually kills the process (a timed-out
        thread cannot be cancelled), while ``asyncio.wait_for`` (with a
        grace period) is the outer backstop — when it fires, the
        ``finally`` removal SIGKILLs the unit. Note: busybox ``timeout``
        (e.g. alpine images) does not fire when it is the container's
        PID 1; on such images the outer backstop alone enforces the
        limit (up to +5s late), preserving the timeout semantics. The
        default ubuntu image (coreutils) hits the 124 path directly.

        Args:
            program: The command to execute
            timeout: Maximum execution time in seconds
            workdir: Optional working directory override

        Returns:
            ExecutionResult with exit code and output. On timeout:
            exit_code=-1, stderr="Command timed out after {n} seconds"
            (contract relied on by AuditedExecutionDevice._is_timed_out).

        Raises:
            RuntimeError: ``docker.level`` is not L2 — this member's
                device has no code-execution capability.
        """
        if self.config.level != "L2":
            raise RuntimeError(
                f"docker.level={self.config.level} 无代码执行能力：run() 仅 L2"
                "（Docker 完整 userland）可用——L1 文件层的装置容器从不启动、"
                "exec 单元从不存在"
            )
        exec_name = f"tlon-agent-{self.agent_id}{EXEC_NAME_MARKER}{uuid.uuid4().hex[:8]}"
        container: Container | None = None
        try:
            # All docker SDK calls are blocking — run them in threads so
            # the event loop stays responsive.
            container = await asyncio.to_thread(
                self.client.containers.create,
                **self._container_kwargs(
                    name=exec_name,
                    command=["timeout", str(timeout), "bash", "-c", program],
                    working_dir=workdir or self.config.working_dir,
                ),
            )
            await asyncio.to_thread(container.start)
            try:
                wait_result = await asyncio.wait_for(
                    asyncio.to_thread(container.wait), timeout=timeout + 5
                )
            except TimeoutError:
                logger.warning("command_timeout", timeout=timeout, command=program)
                return ExecutionResult(
                    exit_code=-1, stdout="", stderr=f"Command timed out after {timeout} seconds"
                )

            stdout_bytes = await asyncio.to_thread(container.logs, stdout=True, stderr=False)
            stderr_bytes = await asyncio.to_thread(container.logs, stdout=False, stderr=True)
            status_code = wait_result.get("StatusCode")
            exit_code: int = status_code if isinstance(status_code, int) else -1
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
        finally:
            if container is not None:
                try:
                    await asyncio.to_thread(container.remove, force=True)
                except Exception as e:
                    logger.warning(
                        "exec_container_remove_error", container_name=exec_name, error=str(e)
                    )

    # ------------------------------------------------------------------
    # File I/O APIs (sandbox-only — Agent never touches host filesystem)
    # ------------------------------------------------------------------

    async def _cabinet_io_retry(self, path: str, call: Callable[[], _T]) -> _T:
        """Run a blocking cabinet I/O call, recovering once from a stale
        container reference.

        The wake lock only serializes assembly, not the session lifecycle
        (issue #87): a config-drift rebuild in ``ensure_running``
        (``container.remove(force=True)``) can race with an in-flight call
        holding the old ``self.container``, surfacing as a daemon 404
        (container already gone) or 409 (removal in progress). Both are
        retried exactly once after re-running ``ensure_running`` to
        reattach to the rebuilt cabinet. A NotFound raised by the retry
        is therefore genuinely a missing path — the container is known to
        exist. Any other APIError propagates unchanged.

        Args:
            path: Container path being accessed (for logging).
            call: Blocking callable run via ``asyncio.to_thread``; it must
                read ``self.container`` at call time so the retry picks up
                the refreshed reference.

        Raises:
            docker.errors.APIError: When the first attempt fails with a
                non-404/409 status, or the retry fails.
        """
        try:
            return await asyncio.to_thread(call)
        except APIError as e:
            if e.status_code not in (404, 409):
                raise
            logger.warning("cabinet_io_retry", path=path, error=str(e))
            await self.ensure_running()
            return await asyncio.to_thread(call)

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

        tar_bytes = await self.fetch_archive(path)

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

    async def fetch_archive(self, path: str) -> bytes:
        """Fetch a path from the container as raw tar bytes.

        Works on files and directories alike — a directory comes back as
        its whole subtree (verified against a live daemon, issue #40).
        This is the archive-only read channel: list/search tools unpack
        the tar host-side instead of running find/grep in the container,
        so they work at L1 where the cabinet never starts.

        Args:
            path: Absolute path inside the container.

        Returns:
            The complete tar archive as bytes.

        Raises:
            FileNotFoundError: If the path does not exist in the container.
            RuntimeError: On daemon errors.
        """
        if not self.container:
            await self.ensure_running()

        if not self.container:
            raise RuntimeError("Container not available")

        try:
            return await self._cabinet_io_retry(path, lambda: self._fetch_archive(path))
        except NotFound as e:
            raise FileNotFoundError(f"Path not found in container: {path}") from e
        except APIError as e:
            logger.error("archive_fetch_error", path=path, error=str(e))
            raise RuntimeError(f"get_archive failed for {path}: {e}") from e

    def _path_stat(self, path: str) -> dict[str, Any] | None:
        """Probe a path's existence via get_archive response headers
        (blocking — run in a thread).

        The stat dict travels in response headers, so the tar stream is
        closed without being consumed and the probe stays cheap even for
        a large directory.

        Returns:
            The daemon's stat dict.

        Raises:
            docker.errors.NotFound: If the path does not exist.
            docker.errors.APIError: On other daemon errors.
        """
        if self.container is None:
            raise RuntimeError("Container not available")
        stream, stat = self.container.get_archive(path)
        close = getattr(stream, "close", None)
        if close is not None:
            close()
        return stat

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
        Automatically creates parent directories (see ``_write_archive``).

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

        try:
            ok = await self._cabinet_io_retry(path, lambda: self._write_archive(path, content))
        except APIError as e:
            logger.error("file_write_error", path=path, error=str(e))
            raise RuntimeError(f"write_file failed for {path}: {e}") from e
        if not ok:
            logger.error("file_write_error", path=path)
            raise RuntimeError(f"write_file failed: put_archive returned False for {path}")

        logger.debug("file_written", path=path, size=len(content))
        return True

    def _write_archive(self, path: str, content: bytes) -> bool:
        """Write a file via put_archive with an in-memory tar (blocking —
        run in a thread).

        Parent creation is archive-only (issue #40): the daemon does
        NOT create missing parents for put_archive (404 verified against a
        live daemon), and a tar entry for an *existing* directory clobbers
        its mode/mtime (also verified), so the tar carries directory
        entries for exactly the missing ancestors and is anchored at the
        deepest existing one (probed via ``_path_stat``). When the parent
        already exists the tar holds the file alone.

        Args:
            path: Absolute path inside the container where the file will be written.
            content: Raw file contents as bytes.

        Returns:
            The daemon's put_archive success flag.

        Raises:
            docker.errors.NotFound: If the anchor probe races a removal.
            docker.errors.APIError: On other daemon errors.
        """
        if self.container is None:
            raise RuntimeError("Container not available")

        # Anchor at the deepest existing ancestor; collect the missing
        # parent dirs (deepest first) along the way.
        parent, _, filename = path.rpartition("/")
        anchor = parent or "/"
        missing: list[str] = []
        while anchor != "/":
            try:
                self._path_stat(anchor)
                break
            except NotFound:
                missing.append(anchor)
                anchor = anchor.rpartition("/")[0] or "/"

        # Build an in-memory tar: missing ancestor dirs (shallowest
        # first), then the file — all relative to the anchor.
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            for directory in reversed(missing):
                info = tarfile.TarInfo(name=directory.removeprefix(anchor).lstrip("/"))
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            name = path.removeprefix(anchor).lstrip("/") if missing else filename
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))

        return bool(self.container.put_archive(anchor, tar_stream.getvalue()))

    async def file_exists(self, path: str) -> bool:
        """Check if a path exists in the container.

        Archive-only probe (issue #40): get_archive's 404 answers the
        question, so no ``test -e`` shell-out is needed — this works at
        L1 where the cabinet never starts.

        Args:
            path: Absolute path inside the container.

        Returns:
            True if the path exists.

        Raises:
            RuntimeError: On daemon errors other than NotFound.
        """
        if not self.container:
            await self.ensure_running()

        try:
            await self._cabinet_io_retry(path, lambda: self._path_stat(path))
        except NotFound:
            return False
        except APIError as e:
            logger.error("file_exists_error", path=path, error=str(e))
            raise RuntimeError(f"file_exists failed for {path}: {e}") from e
        return True
