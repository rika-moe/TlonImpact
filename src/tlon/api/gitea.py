"""Public persistent Gitea lifecycle facade."""

from tlon.base.paths import TlonPaths
from tlon.gitea.service import GiteaService


class GiteaFacade:
    def __init__(self, paths: TlonPaths, *, port: int = 3000) -> None:
        self._service = GiteaService(paths, port=port)

    def status(self) -> dict[str, object]:
        return self._service.status()

    async def ensure_started(self) -> dict[str, object]:
        return await self._service.ensure_started()

    async def shutdown(self) -> None:
        await self._service.shutdown()
