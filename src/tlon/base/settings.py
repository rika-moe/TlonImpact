"""Process-level configuration (env + secrets) for Tlon.

``Settings`` is an immutable snapshot of the process environment,
constructed once at the process entry point (``Settings.from_env()``)
and explicitly injected into the assembly layers — the same discipline
as ``TlonPaths`` (ADR 0019: 入口构造一次，显式注入贯穿各层). There is
no module-level state here: the ``{TLON_HOME}/.env`` fallback load is an
explicit entry-point action (``load_process_env``) taking the entry's
own ``TlonPaths``, so import of this module never touches the
filesystem and never reads ``$TLON_HOME``.

Only process-level infrastructure and secrets live here. Business
configuration (Docker, memory, tool timeouts) lives in AgentConfig.

字段说明集中在 ``from_env`` 的读取点（每行的尾随注释）——
``scripts/gen_reference_docs.py`` 从这里生成 config-reference.md 的
环境变量表，两处不写第二份。
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from tlon.base.paths import TlonPaths


def load_process_env(paths: TlonPaths) -> None:
    """Load ``{TLON_HOME}/.env`` as a fallback source of environment variables.

    Configuration lives with the data, not the checkout: the .env sits in
    the Tlon home directory (``~/.tlon/.env``, or ``$TLON_HOME/.env``).
    Real environment variables always win (``override=False``): the .env
    only fills in variables that are not already set in the process
    environment.

    Entry points call this once with their own ``TlonPaths``, before
    ``Settings.from_env()`` — the .env load must precede any env read.
    """
    env_file = paths.env_file()
    if env_file.exists():
        load_dotenv(env_file, override=False)


@dataclass(frozen=True)
class Settings:
    """Process-level settings snapshot (env + secrets).

    Construct via :meth:`from_env` at the process entry point and inject
    explicitly; runtime components receive the individual values (or the
    snapshot) from their assembler, never read the environment themselves.
    Field documentation lives at the read site in ``from_env`` (the
    reference-doc generator extracts it from there).
    """

    app_name: str
    log_level: str
    debug: bool
    otel_exporter_otlp_endpoint: str | None
    telegram_bot_token: str
    telegram_allowed_users: str
    log_retention_days: int
    container_memory_budget: str
    arena_model: str
    admin_api_token: str
    github_webhook_secret: str
    github_recipient: str
    github_token: str

    @classmethod
    def from_env(cls) -> "Settings":
        """Snapshot the process environment.

        Call after :func:`load_process_env` so the ``{TLON_HOME}/.env``
        fallback values are visible here.
        """
        return cls(
            app_name=os.getenv("APP_NAME", "Tlon"),  # App settings
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            debug=os.getenv("DEBUG", "false").lower() == "true",
            # Observability (OTel): optional OTLP collector, requires the `otlp` extra
            otel_exporter_otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
            # Telegram bot client (optional `telegram` extra)：@BotFather 颁发的
            # bot token；缺失则 `tlon telegram` 拒绝启动
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            # Telegram user-id 白名单（逗号分隔）；为空则拒绝启动
            telegram_allowed_users=os.getenv("TELEGRAM_ALLOWED_USERS", ""),
            # Logging: retention (days) for the daily server log file sink
            log_retention_days=int(os.getenv("TLON_LOG_RETENTION_DAYS", "7")),
            # Society-level container resource budget (issue #24): total memory
            # budget across all agent containers (e.g. "4g"); empty = no budget.
            # Warning-only — exceeding it logs container_memory_budget_exceeded,
            # nothing is enforced.
            container_memory_budget=os.getenv("TLON_CONTAINER_MEMORY_BUDGET", ""),
            # 演武场（docs/design/arena.md 「成本界」）：run 请求未带 model 时的缺省；
            # 空 = 未配置，run 必须显式指定模型（显式优于静默错默认）
            arena_model=os.getenv("TLON_ARENA_MODEL", ""),
            # Admin API auth (ADR 0013): shared Bearer token guarding /admin/*;
            # empty = auth disabled (default, localhost trust boundary)
            admin_api_token=os.getenv("TLON_ADMIN_TOKEN", ""),
            # GitHub webhook ingress (see docs/design/mail.md 「外部来信与信任面」):
            # HMAC secret of the webhook;
            # empty = POST /webhooks/github is not registered at all
            github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
            # GitHub 来信的默认收件人 member id；空 = 发给全体已知人类
            github_recipient=os.getenv("GITHUB_RECIPIENT", ""),
            # GitHub API token（出站：回信 → issue/PR comment）；
            # 空 = 出站禁用，入站不受影响
            github_token=os.getenv("GITHUB_TOKEN", ""),
        )
