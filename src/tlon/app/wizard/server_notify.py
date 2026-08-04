"""Best-effort notification that a new agent was created out-of-process.

The wizard/mirror writes agent.yaml directly under ~/.tlon; a running
server (dream) can hot-adopt the agent via
POST /admin/config/agents/{agent_id}/reload. When no server is running
the notification fails silently and the agent takes effect after the
next server restart (the pre-hot-reload semantics).
"""

import asyncio
from urllib.parse import quote

import aiohttp

from tlon.base.logger import get_logger

logger = get_logger(__name__)

DEFAULT_SERVER_URL = "http://localhost:8080"

# The server is either local and fast or not there at all — never let the
# wizard block on a dead endpoint.
_NOTIFY_TIMEOUT_SECONDS = 2.0


async def _post_reload(agent_id: str, server_url: str) -> None:
    """POST the reload request; the response body is ignored."""
    timeout = aiohttp.ClientTimeout(total=_NOTIFY_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        url = f"{server_url}/admin/config/agents/{quote(agent_id, safe='')}/reload"
        async with session.post(url):
            pass


def notify_server_agent_created(
    agent_id: str, server_url: str = DEFAULT_SERVER_URL
) -> bool:
    """Ask the running server to hot-adopt a freshly created agent.

    Every error is swallowed: an unreachable server is the normal case
    when the wizard runs standalone, and the restart fallback keeps the
    old semantics.

    Args:
        agent_id: id of the freshly created agent
        server_url: base URL of the server's REST API

    Returns:
        True when the request was sent without error, False otherwise
    """
    try:
        asyncio.run(_post_reload(agent_id, server_url))
    except Exception as e:
        logger.debug("server_notify_failed", agent_id=agent_id, error=str(e))
        return False
    logger.info("server_notified_agent_created", agent_id=agent_id)
    return True
