"""Admin env settings endpoints: /admin/settings/env — read/merge-write
{TLON_HOME}/.env (wizard removal F).

LLM 配置已成员级化（ADR 0016）：进程级 `LLM_*` 键全部删除，本端点不再
接受它们——API key 走 litellm 原生的 `{PROVIDER}_API_KEY` 环境变量约定，
模型在成员 agent.yaml / WebUI 成员编辑里配。当前没有可编辑的进程级字段，
端点保留通用的 merge-write 通道。
"""

from aiohttp import web

from tlon.base.logger import get_logger

from .common import RestHandlerBase

# Keep the pre-split logger name so log files keep a stable "logger" field.
logger = get_logger("tlon.app.server.api.rest")

#: ops settings form fields -> .env keys (merge-write semantics: keys
#: not edited are left untouched in the file). Empty since ADR 0016
#: removed the process-level LLM_* keys; add non-LLM fields here as
#: they become editable.
_ENV_FIELDS: dict[str, str] = {}


class SettingsHandlers(RestHandlerBase):
    """Admin env settings route handlers, mixed into ``RESTAPIHandler``."""

    async def handle_get_settings_env(self, request: web.Request) -> web.Response:
        """GET /admin/settings/env - current editable env configuration.

        Returns the .env file path and the effective values of the
        editable fields (``_ENV_FIELDS``; real environment variables win
        over .env).

        Returns:
            200 with ``{path, values}``; 503 when the admin context is
            unavailable.
        """
        if not self.paths:
            return self._json_response(
                {"status": "error", "message": "Admin API not available"}, status=503
            )
        return self._json_response(
            {
                "status": "success",
                "path": str(self.paths.env_file()),
                "values": {},
            }
        )

    async def handle_update_settings_env(self, request: web.Request) -> web.Response:
        """PUT /admin/settings/env - merge-write editable fields to
        {TLON_HOME}/.env.

        Merge semantics: only the fields present in the body and mapped
        in ``_ENV_FIELDS`` are updated; other lines (comments and
        untouched keys) in the .env are kept. The process reads env at
        startup — changes take effect after a dream restart (Settings is
        a process-level singleton).

        Returns:
            200 with ``{path, note, updated}``; 400 invalid body; 503
            admin context unavailable.
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
        updates: dict[str, str] = {}
        for field, env_key in _ENV_FIELDS.items():
            if field in body and body[field] is not None:
                updates[env_key] = str(body[field])

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
