"""Shared plumbing for the REST API handler mixins.

``RestHandlerBase`` declares the constructor-injected attributes (set by
``RESTAPIHandler.__init__`` in app.py) so the per-domain mixins can read
them with mypy-checked types, and hosts the cross-domain helpers
(``_json_response``, CORS preflight).

Runtime touchpoints are the tlon.api domain facades (ADR 0019); the
server never imports runtime internals directly.
"""

import asyncio
import json
from typing import Any

from aiohttp import web

from tlon.api.config import ConfigFacade
from tlon.api.events import EventsFacade
from tlon.api.mail import MailFacade
from tlon.api.members import MembersFacade
from tlon.api.observability import ObservabilityFacade
from tlon.api.prototypes import PrototypesFacade
from tlon_server.core.client_gateway import ClientGateway


class RestHandlerBase:
    """Attribute declarations + shared helpers for the domain mixins.

    The aggregate ``RESTAPIHandler`` assigns every attribute in its
    ``__init__``; the mixins only read them.
    """

    client_gateway: ClientGateway
    members: MembersFacade | None
    mail: MailFacade | None
    prototypes: PrototypesFacade | None
    observability: ObservabilityFacade | None
    configs: ConfigFacade | None
    events: EventsFacade | None
    debug: bool
    admin_token: str | None
    github_ingress: bool
    github_webhook_secret: str | None
    # Active SSE streams: session_id -> frame queue (None sentinel ends
    # the stream; see handle_events / handle_delete_session)
    _sse_queues: dict[str, asyncio.Queue[str | None]]

    def _json_response(self, data: Any, status: int = 200) -> web.Response:
        """Create a JSON response.

        Args:
            data: Data to serialize
            status: HTTP status code

        Returns:
            JSON response
        """
        return web.Response(
            text=json.dumps(data, ensure_ascii=False, default=str),
            status=status,
            content_type="application/json",
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            },
        )

    async def handle_options(self, request: web.Request) -> web.Response:
        """Handle CORS preflight requests."""
        return web.Response(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            },
        )


def _validate_username(username_raw: Any) -> str | None:
    """Normalize a human username; None when invalid.

    The username becomes a directory name — reject empty and anything
    carrying a path separator (traversal guard).
    """
    if not isinstance(username_raw, str):
        return None
    username = username_raw.strip()
    if not username or "/" in username or "\\" in username:
        return None
    return username


def _parse_limit(request: web.Request, default: int = 100, maximum: int = 1000) -> int:
    """Parse the `limit` query parameter with a cap."""
    try:
        return min(int(request.query.get("limit", default)), maximum)
    except ValueError:
        return default
