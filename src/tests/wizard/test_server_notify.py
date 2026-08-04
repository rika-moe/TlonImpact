"""Tests for the wizard's best-effort server notification (P3 hot adoption).

The HTTP layer (_post_reload) is mocked: the contract under test is that
notify_server_agent_created posts to the reload endpoint and swallows
every error (an unreachable server is the normal standalone case).
"""

from unittest.mock import AsyncMock, patch

from tlon.app.wizard.server_notify import (
    DEFAULT_SERVER_URL,
    notify_server_agent_created,
)


class TestNotifyServerAgentCreated:
    def test_success_returns_true(self):
        with patch(
            "tlon.app.wizard.server_notify._post_reload", new=AsyncMock()
        ) as mock_post:
            result = notify_server_agent_created("a-001")

        assert result is True
        mock_post.assert_awaited_once_with("a-001", DEFAULT_SERVER_URL)

    def test_custom_server_url_is_forwarded(self):
        with patch(
            "tlon.app.wizard.server_notify._post_reload", new=AsyncMock()
        ) as mock_post:
            result = notify_server_agent_created("a-001", "http://localhost:9999")

        assert result is True
        mock_post.assert_awaited_once_with("a-001", "http://localhost:9999")

    def test_error_is_swallowed(self):
        with patch(
            "tlon.app.wizard.server_notify._post_reload",
            new=AsyncMock(side_effect=ConnectionRefusedError("server not running")),
        ):
            result = notify_server_agent_created("a-001")

        assert result is False

    def test_unexpected_error_is_swallowed(self):
        with patch(
            "tlon.app.wizard.server_notify._post_reload",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = notify_server_agent_created("a-001")

        assert result is False
