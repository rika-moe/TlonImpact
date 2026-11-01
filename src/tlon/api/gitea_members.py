"""Reconcile member identity without coupling Gitea to the organization layer."""

import asyncio
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress

from filelock import FileLock

from tlon.base.paths import TlonPaths
from tlon.gitea.control import StartupControl
from tlon.gitea.members import MemberManager
from tlon.gitea.service import GiteaService
from tlon.gitea.storage import GiteaError
from tlon.organization.mail.store import MailStore
from tlon.sandbox.gitea_access import GiteaExecutionAccess


class GiteaMembersCoordinator:
    def __init__(
        self,
        paths: TlonPaths,
        service: GiteaService,
        members: Callable[[], Mapping[str, str]],
        *,
        retry_seconds: float = 5,
    ) -> None:
        self._paths = paths
        self._service = service
        self._members = members
        self._retry_seconds = retry_seconds
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._worker_stop = threading.Event()
        self._task: asyncio.Task[None] | None = None
        self._manager: MemberManager | None = None
        self._network = ""
        self._errors: dict[str, str] = {}
        self._state = "not_started"

    def status(self) -> dict[str, object]:
        return {"state": self._state, "errors": self._errors.copy()}

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._worker_stop.clear()
            self._task = asyncio.create_task(self._run())

    async def shutdown(self) -> None:
        self._stop.set()
        self._worker_stop.set()
        if self._task is not None:
            await self._task
        # Also settle a foreground access request that owned the worker lock.
        async with self._lock:
            pass

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.reconcile()
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._retry_seconds)

    def _reconcile(
        self, names: dict[str, str], member_id: str | None
    ) -> GiteaExecutionAccess | None:
        control = StartupControl(self._worker_stop)
        store = MailStore(self._paths.mail_database_file())
        with FileLock(self._paths.gitea_lock_file(), timeout=5):
            control.check()
            if self._manager is None:
                self._manager, self._network = self._service.member_manager()
            active = {key: name for key, name in names.items() if not store.is_retired(key)}
            if member_id is not None:
                if member_id not in active:
                    raise GiteaError("Member is absent or retired; Gitea access denied")
                try:
                    credentials = self._manager.credentials(member_id)
                except GiteaError:
                    pass
                else:
                    return GiteaExecutionAccess(
                        url=f"http://{self._network.removesuffix('-network')}:3000",
                        username=credentials.username,
                        token=credentials.token,
                        network=self._network,
                    )
            self._errors = self._manager.reconcile(active, control=control)
            self._state = "degraded" if self._errors else "ready"
            if member_id is None:
                return None
            if member_id not in active or store.is_retired(member_id):
                raise GiteaError("Member is absent or retired; Gitea access denied")
            credentials = self._manager.credentials(member_id)
            return GiteaExecutionAccess(
                url=f"http://{self._network.removesuffix('-network')}:3000",
                username=credentials.username,
                token=credentials.token,
                network=self._network,
            )

    async def reconcile(self, member_id: str | None = None) -> GiteaExecutionAccess | None:
        async with self._lock:
            if self._stop.is_set() or self._service.status()["state"] != "ready":
                self._state = "unavailable"
                if member_id is not None:
                    raise GiteaError("Gitea is unavailable")
                return None
            names = dict(self._members())
            # Keep ownership of the worker during cancellation: it may be revoking access.
            task = asyncio.create_task(asyncio.to_thread(self._reconcile, names, member_id))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await asyncio.shield(task)
                raise
            except GiteaError:
                if member_id is not None:
                    raise
                self._state = "unavailable"
                self._errors = {"service": "Member synchronization failed; retry pending"}
                return None
            except Exception:
                self._state = "unavailable"
                self._errors = {"service": "Member synchronization failed; retry pending"}
                if member_id is not None:
                    raise GiteaError(
                        "Member Gitea access is unavailable; synchronization pending"
                    ) from None
                return None

    async def execution_access(self, member_id: str) -> GiteaExecutionAccess:
        access = await self.reconcile(member_id)
        assert access is not None
        return access
