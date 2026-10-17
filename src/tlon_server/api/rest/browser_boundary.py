"""Local, same-origin browser boundary; deliberately not user authentication."""

from urllib.parse import urlsplit

from aiohttp import web

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def trusted_request(request: web.Request) -> bool:
    """Validate the actual listening authority before any identity or API action."""
    authority = request.headers.get("Host", "")
    try:
        host = urlsplit(f"//{authority}")
        if (
            host.hostname not in LOOPBACK_HOSTS
            or host.username is not None
            or host.password is not None
            or host.path
            or host.query
            or host.fragment
        ):
            return False
        port = host.port if host.port is not None else (443 if request.secure else 80)
    except ValueError:
        return False
    transport = request.transport
    sockname = transport.get_extra_info("sockname") if transport is not None else None
    if not sockname or port != sockname[1]:
        return False
    origin = request.headers.get("Origin")
    if origin is not None:
        try:
            source = urlsplit(origin)
            source_port = (
                source.port
                if source.port is not None
                else (443 if source.scheme == "https" else 80)
            )
            if (
                source.scheme != request.scheme
                or source.hostname != host.hostname
                or source_port != port
                or source.username is not None
                or source.password is not None
                or source.path
                or source.query
                or source.fragment
            ):
                return False
        except ValueError:
            return False
    return request.headers.get("Sec-Fetch-Site") in (None, "same-origin", "none")


async def protect_response(request: web.Request, response: web.StreamResponse) -> None:
    """Apply to prepared SSE, static files, redirects and HTTP errors alike."""
    for name in list(response.headers):
        if name.lower().startswith("access-control-"):
            del response.headers[name]
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
