"""Configuration management for Tlon.

This module provides environment variable based configuration.
It only handles environment variables, not YAML file configurations.
"""

import os
from pathlib import Path

from dotenv import load_dotenv


def _load_dotenv_files() -> None:
    """Load environment variables from .env files.

    Tries to load in order:
    1. Current directory .env
    2. Project directory (where this file is located) .env
    """
    # First, try current directory
    load_dotenv()

    # Then, try project directory (allows override from project .env)
    project_dir = Path(__file__).parent.parent.parent
    project_env = project_dir / ".env"
    if project_env.exists():
        load_dotenv(project_env, override=True)


# Load environment variables from .env files
_load_dotenv_files()


def get_tlon_home() -> Path:
    """Get the project root directory.

    Priority:
    1. TLON_HOME environment variable (if set and directory exists)
    2. ~/.tlon (default location)

    Returns:
        Path to the project root directory.
    """
    # Check TLON_HOME environment variable first
    tlon_home = os.getenv("TLON_HOME")
    if tlon_home:
        path = Path(tlon_home).expanduser().resolve()
        if path.exists() and path.is_dir():
            return path
        # If TLON_HOME is set but invalid, fall back to default

    # Default location: ~/.tlon
    default_path = Path.home() / ".tlon"
    default_path.mkdir(parents=True, exist_ok=True)
    return default_path


def get_default_data_dir() -> Path:
    """Get the default data directory.

    Returns:
        Path to the data directory (project root / data).
    """
    return get_tlon_home() / "data"


def get_default_config_dir() -> Path:
    """Get the default config directory.

    Returns:
        Path to the config directory (project root / config).
    """
    return get_tlon_home() / "config"


class Settings:
    """Application settings container.

    All settings are loaded from environment variables.
    This class does not define dataclasses, only flat attributes.
    """

    def __init__(self):
        """Initialize settings from environment variables."""
        # App settings
        self.app_name = os.getenv("APP_NAME", "Tlon")
        self.log_level = os.getenv("LOG_LEVEL", "INFO")
        self.data_dir = Path(os.getenv("DATA_DIR", get_default_data_dir()))
        self.debug = os.getenv("DEBUG", "false").lower() == "true"

        # LLM settings
        self.llm_base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.llm_api_key = os.getenv("OPENAI_API_KEY", "")
        self.llm_model_name = os.getenv("OPENAI_MODEL_NAME", "gpt-4")
        self.llm_temperature = float(os.getenv("OPENAI_TEMPERATURE", "1.0"))
        self.llm_max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
        self.llm_base_delay = float(os.getenv("OPENAI_BASE_DELAY", "1.0"))

        # Docker settings
        self.docker_image = os.getenv("DOCKER_DEFAULT_IMAGE", "ubuntu:22.04")
        self.docker_working_dir = "/workspace"
        self.docker_memory_limit = "512m"
        self.docker_cpu_limit = 1.0
        self.docker_network_mode = os.getenv("DOCKER_NETWORK_MODE", "host")

        # Telegram settings
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.telegram_enabled = bool(self.telegram_bot_token and self.telegram_chat_id)

        # Memory settings
        self.memory_max_history = int(os.getenv("MEMORY_MAX_HISTORY", "1000"))
        self.memory_summary_window = int(os.getenv("MEMORY_SUMMARY_WINDOW", "20"))

        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def get_agent_data_dir(self, agent_id: str) -> Path:
        """Get data directory for a specific agent."""
        agent_dir = self.data_dir / "agents" / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        return agent_dir


# Global settings instance
settings = Settings()
