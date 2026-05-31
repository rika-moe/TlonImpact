"""Configuration management for Tlon.

This module provides environment variable based configuration.
It only handles environment variables, not YAML file configurations.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from tlon.base.paths import paths


def _load_dotenv_files() -> None:
    load_dotenv()
    project_dir = Path(__file__).parent.parent.parent
    project_env = project_dir / ".env"
    if project_env.exists():
        load_dotenv(project_env, override=True)


def get_tlon_home() -> Path:
    """Get the Tlon home directory (~/.tlon or $TLON_HOME).

    Prefer paths.tlon_home() in new code.
    """
    return paths.tlon_home()


def get_agent_home(agent_id: str) -> Path:
    """Get per-agent directory — delegates to paths.agent_dir()."""
    return paths.agent_dir(agent_id)


def get_shared_dir() -> Path:
    """Get shared data directory — delegates to paths.shared_dir()."""
    return paths.shared_dir()


def get_default_data_dir() -> Path:
    """Legacy: returns tlon home root. Prefer TlonPaths directly."""
    return paths.tlon_home()


def get_default_config_dir() -> Path:
    """Legacy: config is now per-agent. Returns tlon home root."""
    return paths.tlon_home()


class Settings:
    """Application settings container.

    Only handles process-level infrastructure and secrets.
    Business configuration (Docker, Memory, Tool timeouts) lives in AgentConfig.
    """

    def __init__(self):
        # App settings
        self.app_name = os.getenv("APP_NAME", "Tlon")
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.tlon_home = paths.tlon_home()
        self.shared_dir = paths.shared_dir()
        self.debug = os.getenv("DEBUG", "false").lower() == "true"

        # LLM client settings (secrets and endpoints)
        self.llm_base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.llm_api_key = os.getenv("OPENAI_API_KEY", "")
        self.llm_model_name = os.getenv("OPENAI_MODEL_NAME", "kimi-k2.5")
        self.llm_temperature = float(os.getenv("OPENAI_TEMPERATURE", "1.0"))
        self.llm_max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
        self.llm_base_delay = float(os.getenv("OPENAI_BASE_DELAY", "1.0"))

        self.tlon_home.mkdir(parents=True, exist_ok=True)

    def get_agent_data_dir(self, agent_id: str) -> Path:
        """Get per-agent data directory — delegates to paths.agent_dir()."""
        return paths.agent_dir(agent_id)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _load_dotenv_files()
        _settings = Settings()
    return _settings
