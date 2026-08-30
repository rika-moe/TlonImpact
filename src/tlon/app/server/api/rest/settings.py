"""Admin env settings endpoints: /admin/settings/env — read/merge-write
{TLON_HOME}/.env (wizard removal F).

``get_settings`` is resolved through the ``tlon.app.server.api.rest``
package namespace at call time (lazily, avoiding an import cycle with the
package ``__init__``) so tests can keep monkeypatching
``tlon.app.server.api.rest.get_settings``.
"""

from aiohttp import web

from tlon.base.logger import get_logger
from tlon.base.settings import Settings

from .common import RestHandlerBase

# Keep the pre-split logger name so log files keep a stable "logger" field.
logger = get_logger("tlon.app.server.api.rest")

#: ops settings form fields -> .env keys (merge-write semantics: keys
#: not edited are left untouched in the file).
_ENV_FIELDS: dict[str, str] = {
    "model": "LLM_MODEL",
    "api_key": "LLM_API_KEY",
    "base_url": "LLM_BASE_URL",
    "temperature": "LLM_TEMPERATURE",
}


def _mask_api_key(api_key: str) -> str:
    """Mask an API key for display: ``****`` + last 4 chars (``****`` when
    shorter, ``""`` when unset). Never returns the plain key."""
    if not api_key:
        return ""
    if len(api_key) <= 4:
        return "****"
    return f"****{api_key[-4:]}"


def _get_settings() -> Settings:
    """Resolve ``get_settings`` via the rest package namespace at call time.

    Tests monkeypatch ``tlon.app.server.api.rest.get_settings``; the lazy
    import also avoids an import cycle with the package ``__init__``.
    """
    import tlon.app.server.api.rest as rest_pkg

    return rest_pkg.get_settings()


class SettingsHandlers(RestHandlerBase):
    """Admin env settings route handlers, mixed into ``RESTAPIHandler``."""

    async def handle_get_settings_env(self, request: web.Request) -> web.Response:
        """GET /admin/settings/env - current LLM env configuration.

        Returns the effective values (real environment variables win over
        .env) with the .env file path. The API key is masked
        (``****`` + last 4 chars, empty when unset) — the plain key never
        leaves the process.

        Returns:
            200 with ``{path, configured, values}``; 503 when the admin
            context is unavailable.
        """
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        settings = _get_settings()
        return self._json_response(
            {
                "status": "success",
                "path": str(self.paths.env_file()),
                "configured": bool(settings.llm_api_key),
                "values": {
                    "model": settings.llm_model_name,
                    "api_key": _mask_api_key(settings.llm_api_key),
                    "base_url": settings.llm_base_url,
                    "temperature": settings.llm_temperature,
                },
            }
        )

    async def handle_update_settings_env(self, request: web.Request) -> web.Response:
        """PUT /admin/settings/env - write LLM env config to {TLON_HOME}/.env.

        Merge semantics: only the fields present in the body are updated;
        other lines (comments and untouched keys) in the .env are kept.
        The process reads env at startup — the change takes effect after a
        dream restart (Settings is a process-level singleton).

        ``api_key`` is write-only: an empty value or the masked value
        returned by GET (``****`` + last 4) means "keep the current key"
        and is never written to the .env.

        Returns:
            200 with ``{path, note, updated}``; 400 invalid body; 503 admin
            context unavailable.
        """
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        try:
            body = await request.json()
        except Exception:
            return self._json_response(
                {"status": "error", "message": "invalid JSON body"}, status=400
            )

        # map ops field names -> .env keys; stringify non-string values
        masked_key = _mask_api_key(_get_settings().llm_api_key)
        updates: dict[str, str] = {}
        for field, env_key in _ENV_FIELDS.items():
            if field in body and body[field] is not None:
                value = str(body[field])
                # api_key: empty or the echoed-back masked value = keep as is
                if field == "api_key" and (not value.strip() or value == masked_key):
                    continue
                updates[env_key] = value

        env_file = self.paths.env_file()
        lines: list[str] = []
        if env_file.exists():
            lines = env_file.read_text(encoding="utf-8").splitlines()

        new_lines: list[str] = []
        updated: list[str] = []
        for line in lines:
            stripped = line.strip()
            if "=" in stripped and not stripped.startswith("#"):
                key, _, _ = stripped.partition("=")
                if key in updates:
                    new_lines.append(f"{key}={updates.pop(key)}")
                    updated.append(key)
                    continue
            new_lines.append(line)
        for key, value in updates.items():
            new_lines.append(f"{key}={value}")
            updated.append(key)

        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        logger.info("settings_env_updated", path=str(env_file), keys=sorted(updated))
        return self._json_response(
            {
                "status": "success",
                "path": str(env_file),
                "note": "重启 dream 生效（Settings 是进程级单例）",
                "updated": sorted(updated),
            }
        )
