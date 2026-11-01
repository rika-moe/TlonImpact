"""Async boundary for the locked, persistent service bootstrap."""

import asyncio
import threading

from filelock import FileLock, Timeout

from tlon.base.paths import TlonPaths
from tlon.gitea.control import StartupControl
from tlon.gitea.docker_service import ensure_container
from tlon.gitea.initialize import OPERATOR, ORGANIZATION, initialize
from tlon.gitea.members import MemberManager
from tlon.gitea.projects import ProjectManager
from tlon.gitea.storage import GiteaError, load_records
from tlon.sandbox.docker_client import create_docker_client


class GiteaService:
    def __init__(self, paths: TlonPaths, *, port: int = 3000) -> None:
        if not 1 <= port <= 65535:
            raise ValueError("Gitea port must be between 1 and 65535")
        self._paths = paths
        self._port = port
        self._stop = threading.Event()
        self._task: asyncio.Task[dict[str, object]] | None = None
        self._status: dict[str, object] = {
            "state": "not_started",
            "url": f"http://127.0.0.1:{port}/",
            "message": "Gitea has not started",
            "container_name": None,
            "organization": ORGANIZATION,
            "operator_username": OPERATOR,
            "credentials_file": str(paths.gitea_operator_credentials_file()),
        }

    def status(self) -> dict[str, object]:
        return self._status.copy()

    def member_manager(self) -> tuple[MemberManager, str]:
        """Construct the host-only account client under the caller's instance lock."""
        if self._status["state"] != "ready":
            raise GiteaError("Gitea is unavailable")
        state, credentials = load_records(self._paths, self._port)
        return (
            MemberManager(self._paths, str(self._status["url"]), credentials["admin_password"]),
            f"tlon-gitea-{state['instance_id']}-network",
        )

    def project_manager(self, control: StartupControl) -> ProjectManager:
        """Construct the human governance client under the caller's instance lock."""
        if self._status["state"] != "ready":
            raise GiteaError("Gitea is unavailable")
        _, credentials = load_records(self._paths, self._port)
        return ProjectManager(str(self._status["url"]), credentials["admin_password"], control=control)

    def _ensure(self) -> dict[str, object]:
        try:
            root = self._paths.gitea_dir()
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            control = StartupControl(self._stop)
            lock = FileLock(self._paths.gitea_lock_file())
            while True:
                control.check()
                try:
                    lock.acquire(timeout=0.2)
                    break
                except Timeout:
                    continue
            try:
                state, credentials = load_records(self._paths, self._port)
                self._status["container_name"] = f"tlon-gitea-{state['instance_id']}"
                client = create_docker_client()
                try:
                    client.api.timeout = 5
                    control.check()
                    container = ensure_container(
                        client, self._paths, str(state["instance_id"]), self._port, control=control
                    )
                    initialize(container, str(self._status["url"]), credentials, control=control)
                    control.check()
                finally:
                    client.close()
            finally:
                lock.release()
            self._status.update(
                state="ready",
                message="Gitea ready; human login credentials are in the private host file",
            )
        except GiteaError as exc:
            self._status.update(state="unavailable", message=str(exc))
        except Exception:
            # SDK/HTTP exceptions can contain request bodies and credentials.
            self._status.update(
                state="unavailable",
                message="Gitea startup failed; check Docker availability, port and private storage",
            )
        return self.status()

    async def ensure_started(self) -> dict[str, object]:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._status.update(state="starting", message="Starting Gitea")
            self._task = asyncio.create_task(asyncio.to_thread(self._ensure))
        try:
            return await asyncio.shield(self._task)
        except asyncio.CancelledError:
            self._stop.set()
            await asyncio.shield(self._task)
            raise

    async def shutdown(self) -> None:
        self._stop.set()
        if self._task is not None:
            await asyncio.shield(self._task)
