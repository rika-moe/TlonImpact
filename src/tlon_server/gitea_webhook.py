"""Dedicated machine ingress for authenticated Gitea notifications."""

from aiohttp import web

from tlon.api.gitea_webhooks import GiteaWebhookCoordinator, GiteaWebhookError


class GiteaWebhookServer:
    """Serve only webhook delivery; the human administration API stays loopback."""

    def __init__(
        self,
        coordinator: GiteaWebhookCoordinator,
        port: int,
        host: str = "0.0.0.0",
    ) -> None:
        self._coordinator = coordinator
        self.host = host
        self.port = port
        self._runner: web.AppRunner | None = None

    def create_app(self) -> web.Application:
        app = web.Application(client_max_size=1024 * 1024)
        app.router.add_post("/gitea/{instance_id}", self._receive)
        return app

    async def _receive(self, request: web.Request) -> web.Response:
        if request.content_type != "application/json":
            return web.json_response({"error": "Expected application/json"}, status=415)
        try:
            result = await self._coordinator.receive(
                request.match_info["instance_id"],
                request.headers.get("X-Gitea-Event-Type", ""),
                request.headers.get("X-Gitea-Delivery", ""),
                request.headers.get("X-Gitea-Signature", ""),
                await request.read(),
            )
        except GiteaWebhookError as exc:
            return web.json_response({"error": str(exc)}, status=exc.status)
        return web.json_response(result)

    async def start(self) -> None:
        if self._runner is not None:
            return
        runner = web.AppRunner(self.create_app(), access_log=None)
        try:
            await runner.setup()
            await web.TCPSite(runner, self.host, self.port).start()
            self.port = runner.addresses[0][1]
        except BaseException:
            await runner.cleanup()
            raise
        self._runner = runner

    async def stop(self) -> None:
        runner, self._runner = self._runner, None
        if runner is not None:
            self._coordinator.unavailable("Webhook listener is offline")
            await runner.cleanup()
