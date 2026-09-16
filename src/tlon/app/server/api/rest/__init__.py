"""REST API package (HTTP endpoints + SSE event stream).

Formerly the single-module ``api/rest.py``; split by route domain ():
``app`` (route table + RESTServer + core endpoints), ``common`` (shared
base/helpers), ``mail``, ``sessions``, ``admin_agents``,
``admin_llm``, ``admin_prototypes``, ``observability``.

check_no_reexport bans ``from ... import`` in ``__init__.py``, so the
public names are bound via plain module imports + attribute assignment —
this keeps ``from tlon.app.server.api.rest import RESTAPIHandler`` (and
``RESTServer`` / ``WEBUI_DIST_DIR``) working for existing callers.
"""

from pathlib import Path

import tlon.app.server.api.rest.app as _app
import tlon.app.server.api.rest.sessions as _sessions

RESTAPIHandler = _app.RESTAPIHandler
RESTServer = _app.RESTServer
WEBUI_DIST_DIR: Path = _app.WEBUI_DIST_DIR
# Bound at package level because tests monkeypatch
# ``tlon.app.server.api.rest.SSE_HEARTBEAT_SECONDS``; the SSE handler reads
# it through this namespace at call time (see sessions.py).
SSE_HEARTBEAT_SECONDS = _sessions.SSE_HEARTBEAT_SECONDS
