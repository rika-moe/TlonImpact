"""Configuration management for Tlon.

This module provides environment variable based configuration.
It only handles environment variables, not YAML file configurations.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


def _load_dotenv_files() -> None:
    load_dotenv()
    project_dir = Path(__file__).parent.parent.parent
    project_env = project_dir / ".env"
    if project_env.exists():
        load_dotenv(project_env, override=True)


def get_tlon_home() -> Path:
    tlon_home = os.getenv("TLON_HOME")
    if tlon_home:
        path = Path(tlon_home).expanduser().resolve()
        if path.exists() and path.is_dir():
            return path
    default_path = Path.home() / ".tlon"
    default_path.mkdir(parents=True, exist_ok=True)
    return default_path


def get_default_data_dir() -> Path:
    return get_tlon_home() / "data"


def get_default_config_dir() -> Path:
    return get_tlon_home() / "config"


class Settings:
    """Application settings container.

    Only handles process-level infrastructure and secrets.
    Business configuration (Docker, Memory, Tool timeouts) lives in AgentConfig.
    """

    def __init__(self):
        # App settings
        self.app_name = os.getenv("APP_NAME", "Tlon")
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.data_dir = Path(os.getenv("DATA_DIR", get_default_data_dir()))
        self.debug = os.getenv("DEBUG", "false").lower() == "true"

        # LLM client settings (secrets and endpoints)
        self.llm_base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.llm_api_key = os.getenv("OPENAI_API_KEY", "")
        self.llm_model_name = os.getenv("OPENAI_MODEL_NAME", "kimi-k2.5")
        self.llm_temperature = float(os.getenv("OPENAI_TEMPERATURE", "1.0"))
        self.llm_max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
        self.llm_base_delay = float(os.getenv("OPENAI_BASE_DELAY", "1.0"))

        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def get_agent_data_dir(self, agent_id: str) -> Path:
        agent_dir = self.data_dir / "agents" / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        return agent_dir


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _load_dotenv_files()
        _settings = Settings()
    return _settings
