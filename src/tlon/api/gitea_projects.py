"""Local human governance of upstream repositories and reviewer membership."""

import asyncio
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from typing import TypeVar

from filelock import FileLock

from tlon.base.paths import TlonPaths
from tlon.gitea.control import StartupControl
from tlon.gitea.members import member_username
from tlon.gitea.projects import ProjectManager
from tlon.gitea.service import GiteaService
from tlon.gitea.storage import GiteaError
from tlon.organization.mail.store import MailStore

_T = TypeVar("_T")


class GiteaProjectError(Exception):
    def __init__(self, message: str, *, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


class GiteaProjectsCoordinator:
    def __init__(
        self,
        paths: TlonPaths,
        service: GiteaService,
        members: Callable[[], Mapping[str, str]] | None,
    ) -> None:
        self._paths = paths
        self._service = service
        self._members = members
        self._stopped = False
        self._lock = asyncio.Lock()
        self._control: StartupControl | None = None

    async def shutdown(self) -> None:
        self._stopped = True
        if self._control is not None:
            self._control.stop.set()
        async with self._lock:
            pass

    async def _run(self, action: Callable[[ProjectManager], _T]) -> _T:
        async with self._lock:
            if self._stopped or self._service.status()["state"] != "ready":
                raise GiteaProjectError("Gitea 尚未就绪。", status=503)
            control = StartupControl(threading.Event())
            self._control = control

            def execute() -> _T:
                with FileLock(self._paths.gitea_lock_file(), timeout=5):
                    control.check()
                    return action(self._service.project_manager(control))

            task = asyncio.create_task(asyncio.to_thread(execute))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                control.stop.set()
                # Repeated request cancellation must not release the instance lock
                # while the synchronous worker can still mutate Gitea.
                while not task.done():
                    with suppress(Exception, asyncio.CancelledError):
                        await asyncio.shield(task)
                with suppress(Exception, asyncio.CancelledError):
                    task.result()
                raise
            except GiteaProjectError:
                raise
            except GiteaError as exc:
                raise GiteaProjectError(str(exc)) from None
            except Exception:
                raise GiteaProjectError(
                    "Gitea 操作失败，请刷新实际状态后重试。", status=503
                ) from None
            finally:
                self._control = None

    def _active(self) -> dict[str, str]:
        # Read the provider snapshot under the instance lock, never before queueing.
        names = dict(self._members()) if self._members is not None else {}
        store = MailStore(self._paths.mail_database_file())
        return {key: name for key, name in names.items() if not store.is_retired(key)}

    async def list_projects(self) -> dict[str, object]:
        def action(manager: ProjectManager) -> dict[str, object]:
            return {
                "projects": manager.list_projects(),
                "members": [
                    {"id": key, "name": name, "username": member_username(key)}
                    for key, name in self._active().items()
                ],
            }

        return await self._run(action)

    async def configure(self, body: object) -> dict[str, object]:
        if (
            not isinstance(body, dict)
            or set(body) != {"name", "create", "default_branch", "protected_branches"}
            or not isinstance(body["name"], str)
            or type(body["create"]) is not bool
            or not isinstance(body["default_branch"], str)
            or not isinstance(body["protected_branches"], list)
            or len(body["protected_branches"]) > 32
            or not all(isinstance(value, str) for value in body["protected_branches"])
        ):
            raise GiteaProjectError("项目参数无效。", status=400)
        return await self._run(
            lambda manager: manager.configure(
                body["name"],
                create=body["create"],
                default_branch=body["default_branch"],
                protected_branches=body["protected_branches"],
            )
        )

    async def set_reviewer(self, name: str, member_id: str, body: object) -> dict[str, object]:
        if (
            not isinstance(body, dict)
            or set(body) != {"enabled"}
            or type(body["enabled"]) is not bool
        ):
            raise GiteaProjectError("reviewer 参数无效。", status=400)

        def action(manager: ProjectManager) -> dict[str, object]:
            if member_id not in self._active():
                raise GiteaProjectError("成员不存在或已退休。")
            username = member_username(member_id)
            result = manager.set_reviewer(name, username, body["enabled"])
            if body["enabled"] and MailStore(self._paths.mail_database_file()).is_retired(
                member_id
            ):
                manager.set_reviewer(name, username, False)
                raise GiteaProjectError("成员已退休，未授予 reviewer 权限。")
            return result

        return await self._run(action)
