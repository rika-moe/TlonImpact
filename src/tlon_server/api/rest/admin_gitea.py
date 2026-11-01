"""Human-only upstream governance on the existing local browser boundary."""

from aiohttp import web

from tlon.api.gitea_projects import GiteaProjectError

from .common import RestHandlerBase


class AdminGiteaHandlers(RestHandlerBase):
    async def handle_gitea_projects(self, request: web.Request) -> web.Response:
        """List actual upstream governance and active member identities."""
        assert self.gitea is not None
        try:
            return self._json_response(await self.gitea.projects.list_projects())
        except GiteaProjectError as exc:
            return self._json_response({"error": str(exc)}, status=exc.status)

    async def handle_configure_gitea_project(self, request: web.Request) -> web.Response:
        """Create/adopt a private upstream and protect its integration branches."""
        assert self.gitea is not None
        try:
            body = await request.json()
        except (ValueError, TypeError):
            return self._json_response({"error": "项目参数无效。"}, status=400)
        try:
            return self._json_response(await self.gitea.projects.configure(body))
        except GiteaProjectError as exc:
            return self._json_response({"error": str(exc)}, status=exc.status)

    async def handle_gitea_reviewer(self, request: web.Request) -> web.Response:
        """Explicitly grant/revoke upstream reviewer's merge authority."""
        assert self.gitea is not None
        try:
            body = await request.json()
        except (ValueError, TypeError):
            return self._json_response({"error": "reviewer 参数无效。"}, status=400)
        try:
            return self._json_response(
                await self.gitea.projects.set_reviewer(
                    request.match_info["name"], request.match_info["member_id"], body
                )
            )
        except GiteaProjectError as exc:
            return self._json_response({"error": str(exc)}, status=exc.status)
