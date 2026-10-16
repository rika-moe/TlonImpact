"""Single source of truth for the package version (issue #144).

Everything the process reports about its own version — the CLI
``--version`` flag, the ``/health`` payload (the WebUI top bar shows
it) — reads the installed distribution metadata here. The version is
edited in exactly one place: ``pyproject.toml``. Previously three
hardcoded strings had drifted apart (0.1.0 / 0.2.0 / pyproject's).

``tlon_client.sdk`` cannot import ``tlon`` (import-linter contract), so
the SDK reads the same distribution metadata directly — the whole
project ships as the single ``tlon-impact`` distribution.
"""

import importlib.metadata

#: The installed ``tlon-impact`` distribution version (pyproject.toml).
VERSION: str = importlib.metadata.version("tlon-impact")
