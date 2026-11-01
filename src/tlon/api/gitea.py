"""Public persistent Gitea lifecycle facade."""

from collections.abc import Callable, Mapping

from tlon.api.gitea_members import GiteaMembersCoordinator
from tlon.api.gitea_projects import GiteaProjectsCoordinator
from tlon.base.paths import TlonPaths
from tlon.gitea.service import GiteaService
from tlon.gitea.storage import GiteaError
from tlon.sandbox.gitea_access import GiteaExecutionAccess


class GiteaFacade:
    def __init__(
        self,
        paths: TlonPaths,
        *,
        port: int = 3000,
        members: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        self._service = GiteaService(paths, port=port)
        self.projects = GiteaProjectsCoordinator(paths, self._service, members)
        self._members = GiteaMembersCoordinator(paths, self._service, members) if members else None

    def status(self) -> dict[str, object]:
        status = self._service.status()
        if self._members is not None:
            status["members"] = self._members.status()
        return status

    async def ensure_started(self) -> dict[str, object]:
        await self._service.ensure_started()
        if self._members is not None:
            self._members.start()
        return self.status()

    async def shutdown(self) -> None:
        await self.projects.shutdown()
        if self._members is not None:
            await self._members.shutdown()
        await self._service.shutdown()

    async def execution_access(self, member_id: str) -> GiteaExecutionAccess:
        if self._members is None:
            raise GiteaError("Member synchronization is not configured")
        return await self._members.execution_access(member_id)
