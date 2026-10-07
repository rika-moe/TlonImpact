"""Configuration management for Tlon.

This module provides environment variable based configuration.
It only handles environment variables, not YAML file configurations.

Settings is process-level state (env + secrets), so it resolves the
process Tlon home itself: one private ``TlonPaths()`` constructed from
``$TLON_HOME`` / ``~/.tlon`` at first use. Runtime components never go
through here — they receive an explicit ``TlonPaths`` by injection.
"""

import os

from dotenv import load_dotenv

from tlon.base.paths import TlonPaths

_paths = TlonPaths()


def _load_dotenv_files() -> None:
    """Load ``{TLON_HOME}/.env`` as a fallback source of environment variables.

    Configuration lives with the data, not the checkout: the .env sits in
    the Tlon home directory (``~/.tlon/.env``, or ``$TLON_HOME/.env``).
    Real environment variables always win (``override=False``): the .env
    only fills in variables that are not already set in the process
    environment.
    """
    env_file = _paths.env_file()
    if env_file.exists():
        load_dotenv(env_file, override=False)


class Settings:
    """Application settings container.

    Only handles process-level infrastructure and secrets.
    Business configuration (Docker, Memory, Tool timeouts) lives in AgentConfig.
    """

    def __init__(self) -> None:
        # App settings
        self.app_name = os.getenv("APP_NAME", "Tlon")
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.tlon_home = _paths.tlon_home()
        self.shared_dir = _paths.shared_dir()
        self.debug = os.getenv("DEBUG", "false").lower() == "true"

        # Observability (OTel): optional OTLP collector, requires the `otlp` extra
        self.otel_exporter_otlp_endpoint: str | None = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

        # Telegram bot client (optional `telegram` extra)
        self.telegram_bot_token = os.getenv(
            "TELEGRAM_BOT_TOKEN", ""
        )  # @BotFather 颁发的 bot token；缺失则 `tlon telegram` 拒绝启动
        self.telegram_allowed_users = os.getenv(
            "TELEGRAM_ALLOWED_USERS", ""
        )  # Telegram user-id 白名单（逗号分隔）；为空则拒绝启动

        # Logging: retention (days) for the daily server log file sink
        self.log_retention_days = int(os.getenv("TLON_LOG_RETENTION_DAYS", "7"))

        # Society-level container resource budget (issue #24): total memory
        # budget across all agent containers (e.g. "4g"); empty = no budget.
        # Warning-only — exceeding it logs container_memory_budget_exceeded,
        # nothing is enforced.
        self.container_memory_budget = os.getenv(
            "TLON_CONTAINER_MEMORY_BUDGET", ""
        )  # 社会级容器内存总预算；空 = 不设，仅 warning 不强制

        # 演武场（docs/design/arena.md 「成本界」）：run 请求未带 model 时的缺省；
        # 空 = 未配置，run 必须显式指定模型（显式优于静默错默认）
        self.arena_model = os.getenv(
            "TLON_ARENA_MODEL", ""
        )  # 演武场 run 的缺省模型（provider/model）；空 = run 必须显式指定 model

        # Admin API auth (ADR 0013): shared Bearer token guarding /admin/*;
        # empty = auth disabled (default, localhost trust boundary)
        self.admin_api_token = os.getenv(
            "TLON_ADMIN_TOKEN", ""
        )  # 配置后 /admin/* 需 Authorization: Bearer；空 = 关闭

        # GitHub webhook ingress (see docs/design/mail.md 「外部来信与信任面」):
        # HMAC secret of the webhook;
        # empty = POST /webhooks/github is not registered at all
        self.github_webhook_secret = os.getenv(
            "GITHUB_WEBHOOK_SECRET", ""
        )  # GitHub webhook 配置的 secret；空 = 端点不注册
        self.github_recipient = os.getenv(
            "GITHUB_RECIPIENT", ""
        )  # GitHub 来信的默认收件人 member id；空 = 发给全体已知人类
        self.github_token = os.getenv(
            "GITHUB_TOKEN", ""
        )  # GitHub API token（出站：回信 → issue/PR comment）；空 = 出站禁用，入站不受影响

        self.tlon_home.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _load_dotenv_files()
        _settings = Settings()
    return _settings
