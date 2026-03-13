"""Configuration management for Tlon."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

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


def get_project_root() -> Path:
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
    return get_project_root() / "data"


def get_default_config_dir() -> Path:
    """Get the default config directory.
    
    Returns:
        Path to the config directory (project root / config).
    """
    return get_project_root() / "config"


@dataclass
class AppConfig:
    """Application configuration."""
    
    name: str = "Tlon"
    log_level: str = "INFO"
    data_dir: Path = field(default_factory=get_default_data_dir)
    debug: bool = False
    
    def __post_init__(self):
        """Ensure data directory exists."""
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class LLMConfig:
    """LLM configuration."""
    
    base_url: str = "https://api.openai.com/v1"
    auth_token: str = ""
    model_name: str = "gpt-4"
    temperature: float = 0.7
    max_retries: int = 3
    base_delay: float = 1.0
    
    def __post_init__(self):
        """Validate configuration."""
        # Always prefer environment variables
        env_token = os.getenv("OPENAI_API_KEY", "")
        if env_token:
            self.auth_token = env_token
        
        env_base_url = os.getenv("OPENAI_BASE_URL")
        if env_base_url:
            self.base_url = env_base_url
        
        env_model = os.getenv("OPENAI_MODEL_NAME")
        if env_model:
            self.model_name = env_model
        
        env_temp = os.getenv("OPENAI_TEMPERATURE")
        if env_temp:
            self.temperature = float(env_temp)


@dataclass
class DockerConfig:
    """Docker configuration."""
    
    image: str = "ubuntu:22.04"
    working_dir: str = "/workspace"
    memory_limit: str = "512m"
    cpu_limit: float = 1.0
    network_mode: str = "host"


@dataclass
class TelegramConfig:
    """Telegram bot configuration."""
    
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    
    def __post_init__(self):
        """Load from environment."""
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", self.bot_token)
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", self.chat_id)
        self.enabled = bool(self.bot_token and self.chat_id)


@dataclass
class MemoryConfig:
    """Memory configuration."""
    
    max_history: int = 1000
    summary_window: int = 20


class Settings:
    """Application settings container."""
    
    def __init__(self):
        """Initialize settings from environment variables."""
        self.app = AppConfig(
            name=os.getenv("APP_NAME", "Tlon"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            data_dir=Path(os.getenv("DATA_DIR", get_default_data_dir())),
            debug=os.getenv("DEBUG", "false").lower() == "true",
        )
        
        self.llm = LLMConfig()
        
        self.docker = DockerConfig(
            image=os.getenv("DOCKER_DEFAULT_IMAGE", "ubuntu:22.04"),
            network_mode=os.getenv("DOCKER_NETWORK_MODE", "host"),
        )
        
        self.telegram = TelegramConfig()
        
        self.memory = MemoryConfig(
            max_history=int(os.getenv("MEMORY_MAX_HISTORY", "1000")),
            summary_window=int(os.getenv("MEMORY_SUMMARY_WINDOW", "20")),
        )
    
    def get_agent_data_dir(self, agent_id: str) -> Path:
        """Get data directory for a specific agent."""
        agent_dir = self.app.data_dir / "agents" / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        return agent_dir


# Global settings instance
settings = Settings()
