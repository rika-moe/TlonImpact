"""Machine ingress is separate from the browser administration surface."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tlon.api.gitea_webhooks import GiteaWebhookCoordinator, GiteaWebhookError
from tlon_server.gitea_webhook import GiteaWebhookServer


def _coordinator() -> MagicMock:
    coordinator = MagicMock(spec=GiteaWebhookCoordinator)
    coordinator.receive = AsyncMock(return_value={"accepted": True})
    return coordinator


async def test_machine_ingress_forwards_raw_body_and_headers() -> None:
    coordinator = _coordinator()
    server = GiteaWebhookServer(coordinator, 0)
    async with TestClient(TestServer(server.create_app())) as client:
        body = b'{"action": "opened"}'
        response = await client.post(
            "/gitea/instance",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Gitea-Event-Type": "issues",
                "X-Gitea-Delivery": "delivery",
                "X-Gitea-Signature": "signature",
            },
        )
        assert response.status == 200
        assert await response.json() == {"accepted": True}
        coordinator.receive.assert_awaited_once_with(
            "instance", "issues", "delivery", "signature", body
        )
        assert "Access-Control-Allow-Origin" not in response.headers
        for path in ("/", "/ui/", "/admin/gitea", "/health"):
            assert (await client.get(path)).status == 404
        assert (await client.get("/gitea/instance")).status == 405
        assert (await client.options("/gitea/instance")).status == 405


async def test_rejects_wrong_content_type_and_oversized_body() -> None:
    coordinator = _coordinator()
    async with TestClient(TestServer(GiteaWebhookServer(coordinator, 0).create_app())) as client:
        assert (await client.post("/gitea/id", data="{}")).status == 415
        response = await client.post(
            "/gitea/id", data=b"x" * (1024 * 1024 + 1), headers={"Content-Type": "application/json"}
        )
        assert response.status == 413
        coordinator.receive.assert_not_awaited()


async def test_facade_owns_authentication_and_json_validation() -> None:
    coordinator = _coordinator()
    coordinator.receive.side_effect = GiteaWebhookError(401, "Invalid signature")
    async with TestClient(TestServer(GiteaWebhookServer(coordinator, 0).create_app())) as client:
        response = await client.post(
            "/gitea/id", data=b"\xff", headers={"Content-Type": "application/json"}
        )
        assert response.status == 401
        assert await response.json() == {"error": "Invalid signature"}
        coordinator.receive.assert_awaited_once_with("id", "", "", "", b"\xff")


async def test_start_failure_cleans_runner_and_can_retry() -> None:
    server = GiteaWebhookServer(_coordinator(), 0, "127.0.0.1")
    with (
        patch("tlon_server.gitea_webhook.web.AppRunner") as runner_type,
        patch("tlon_server.gitea_webhook.web.TCPSite") as site_type,
    ):
        runner = runner_type.return_value
        runner.setup = AsyncMock()
        runner.cleanup = AsyncMock()
        runner.addresses = [("127.0.0.1", 12345)]
        site_type.return_value.start = AsyncMock(side_effect=OSError("occupied"))
        with pytest.raises(OSError, match="occupied"):
            await server.start()
        runner.cleanup.assert_awaited_once()
        assert server._runner is None
        site_type.return_value.start.side_effect = None
        await server.start()
        assert server.port == 12345
        await server.start()
        assert site_type.return_value.start.await_count == 2
        await server.stop()
        await server.stop()
        assert runner.cleanup.await_count == 2


async def test_start_cancellation_cleans_runner() -> None:
    import asyncio

    server = GiteaWebhookServer(_coordinator(), 0)
    with patch("tlon_server.gitea_webhook.web.AppRunner") as runner_type:
        runner = runner_type.return_value
        runner.setup = AsyncMock(side_effect=asyncio.CancelledError)
        runner.cleanup = AsyncMock()
        with pytest.raises(asyncio.CancelledError):
            await server.start()
        runner.cleanup.assert_awaited_once()
        assert server._runner is None
