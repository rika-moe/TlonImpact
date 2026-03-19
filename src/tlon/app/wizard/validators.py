"""Wizard validators for input validation.

Provides validation functions for user input and environment checks.
Note: Data validation (YAML structure) is handled by ConfigValidator in base.config.
"""

import re
import sys
from pathlib import Path

# Re-export ConfigValidator for backward compatibility
from tlon.base.config.validator import ConfigValidator
from tlon.base.logger import get_logger

logger = get_logger(__name__)


class EnvironmentChecker:
    """Check system environment for Tlon requirements."""

    def __init__(self):
        """Initialize the environment checker."""
        self.results = []
        self.warnings = []
        self.errors = []

    def check_all(self) -> tuple[bool, list, list]:
        """Run all environment checks.

        Returns:
            Tuple of (success, warnings, errors)
        """
        self.check_python_version()
        self.check_docker()
        self.check_docker_compose()
        self.check_disk_space()
        self.check_network()
        self.check_env_file()

        success = len(self.errors) == 0
        return success, self.warnings, self.errors

    def check_python_version(self) -> bool:
        """Check Python version >= 3.11.

        Returns:
            True if version is compatible
        """
        version = sys.version_info
        if version.major >= 3 and version.minor >= 11:
            self.results.append(
                ("Python版本", f"{version.major}.{version.minor}.{version.micro}", True)
            )
            return True
        else:
            self.results.append(
                ("Python版本", f"{version.major}.{version.minor}.{version.micro}", False)
            )
            self.errors.append(f"Python版本需要 >= 3.11, 当前为 {version.major}.{version.minor}")
            return False

    def check_docker(self) -> bool:
        """Check if Docker is installed and running.

        Returns:
            True if Docker is available
        """
        try:
            import subprocess

            result = subprocess.run(
                ["docker", "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                # Check if docker daemon is running
                ps_result = subprocess.run(
                    ["docker", "ps"], capture_output=True, text=True, timeout=5
                )
                if ps_result.returncode == 0:
                    self.results.append(("Docker", version, True))
                    return True
                else:
                    self.results.append(("Docker", "已安装但未运行", False))
                    self.errors.append("Docker守护进程未运行，请启动 Docker")
                    return False
            else:
                self.results.append(("Docker", "未安装", False))
                self.errors.append("Docker未安装，请安装 Docker")
                return False
        except Exception as e:
            self.results.append(("Docker", f"检查失败: {e}", False))
            self.errors.append(f"Docker检查失败: {e}")
            return False

    def check_docker_compose(self) -> bool:
        """Check if Docker Compose is installed.

        Returns:
            True if Docker Compose is available
        """
        try:
            import subprocess

            # Try docker compose (v2) first
            result = subprocess.run(
                ["docker", "compose", "version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                self.results.append(("Docker Compose", version, True))
                return True

            # Try docker-compose (v1)
            result = subprocess.run(
                ["docker-compose", "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                version = result.stdout.strip()
                self.results.append(("Docker Compose", version, True))
                return True

            self.results.append(("Docker Compose", "未安装", False))
            self.warnings.append("Docker Compose未安装，部分功能可能受限")
            return False
        except Exception as e:
            self.results.append(("Docker Compose", f"检查失败: {e}", False))
            self.warnings.append(f"Docker Compose检查失败: {e}")
            return False

    def check_disk_space(self) -> bool:
        """Check available disk space.

        Returns:
            True if sufficient space is available
        """
        try:
            import shutil

            stat = shutil.disk_usage("")
            free_gb = stat.free / (1024**3)

            if free_gb >= 5:
                self.results.append(("磁盘空间", f"{free_gb:.1f}GB 可用", True))
                return True
            else:
                self.results.append(("磁盘空间", f"{free_gb:.1f}GB 可用", False))
                self.warnings.append(f"磁盘空间不足: {free_gb:.1f}GB (建议 >= 5GB)")
                return False
        except Exception as e:
            self.results.append(("磁盘空间", f"检查失败: {e}", False))
            self.warnings.append(f"磁盘空间检查失败: {e}")
            return False

    def check_network(self) -> bool:
        """Check network connectivity.

        Returns:
            True if network is available
        """
        try:
            import urllib.request

            # Try to connect to a reliable host
            urllib.request.urlopen("https://www.google.com", timeout=5)
            self.results.append(("网络连接", "正常", True))
            return True
        except Exception:
            # Try another host
            try:
                urllib.request.urlopen("https://www.baidu.com", timeout=5)
                self.results.append(("网络连接", "正常", True))
                return True
            except Exception as e:
                self.results.append(("网络连接", "异常", False))
                self.warnings.append(f"网络连接可能受限: {e}")
                return False

    def check_env_file(self) -> bool:
        """Check if .env file exists.

        Returns:
            True if .env exists
        """
        env_path = Path(".env")
        if env_path.exists():
            # Check if OPENAI_API_KEY is set
            content = env_path.read_text()
            if "OPENAI_API_KEY" in content:
                self.results.append((".env文件", "存在且包含API配置", True))
                return True
            else:
                self.results.append((".env文件", "存在但未配置API", False))
                self.warnings.append(".env文件存在但未配置 OPENAI_API_KEY")
                return False
        else:
            self.results.append((".env文件", "不存在", False))
            self.warnings.append(".env文件不存在，需要创建")
            return False

    def get_summary(self) -> str:
        """Get a summary of all checks.

        Returns:
            Formatted summary string
        """
        lines = ["环境诊断结果:"]
        lines.append("")

        for name, value, success in self.results:
            status = "✓" if success else "✗"
            lines.append(f"  {status} {name}: {value}")

        if self.warnings:
            lines.append("")
            lines.append("警告:")
            for warning in self.warnings:
                lines.append(f"  ⚠ {warning}")

        if self.errors:
            lines.append("")
            lines.append("错误:")
            for error in self.errors:
                lines.append(f"  ✗ {error}")

        return "\n".join(lines)


class APIValidator:
    """Validate API connectivity."""

    @staticmethod
    def validate_api_key(api_key: str) -> tuple[bool, str]:
        """Validate API key format.

        Args:
            api_key: The API key to validate

        Returns:
            Tuple of (valid, message)
        """
        if not api_key:
            return False, "API Key 不能为空"

        if len(api_key) < 10:
            return False, "API Key 格式不正确（太短）"

        # Basic format check (most keys start with sk-)
        if not api_key.startswith("sk-"):
            return True, "API Key 格式可能不正确（建议以 sk- 开头）"

        return True, "API Key 格式正确"

    @staticmethod
    def test_api_connection(base_url: str, api_key: str, model: str) -> tuple[bool, str, float]:
        """Test API connection.

        Args:
            base_url: API base URL
            api_key: API key
            model: Model name

        Returns:
            Tuple of (success, message, response_time_ms)
        """
        import time

        start_time = time.time()

        try:
            from openai import OpenAI

            client = OpenAI(
                base_url=base_url,
                api_key=api_key,
            )

            # Try to list models or make a simple completion
            try:
                models = client.models.list()
                elapsed = (time.time() - start_time) * 1000

                # Check if requested model is available
                model_ids = [m.id for m in models.data]
                if model in model_ids:
                    return True, f"连接成功！模型 {model} 可用", elapsed
                else:
                    available = ", ".join(model_ids[:5])
                    return (
                        True,
                        f"连接成功！但模型 {model} 不在列表中。可用: {available}...",
                        elapsed,
                    )
            except Exception:
                # Try a simple completion
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "Hello"}],
                    max_tokens=5,
                )
                elapsed = (time.time() - start_time) * 1000
                return True, f"连接成功！响应时间 {elapsed:.0f}ms", elapsed

        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            return False, f"连接失败: {str(e)[:100]}", elapsed


class AgentValidator:
    """Validate Agent user input (not data validation - that's ConfigValidator's job)."""

    ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$", re.IGNORECASE)

    @classmethod
    def validate_agent_id(cls, agent_id: str) -> tuple[bool, str]:
        """Validate agent ID format for user input.

        Args:
            agent_id: Agent ID to validate

        Returns:
            Tuple of (valid, message)
        """
        if not agent_id:
            return False, "Agent ID 不能为空"

        if len(agent_id) < 2:
            return False, "Agent ID 至少需要 2 个字符"

        if len(agent_id) > 32:
            return False, "Agent ID 不能超过 32 个字符"

        if not cls.ID_PATTERN.match(agent_id):
            return False, "Agent ID 只能包含字母、数字、下划线和连字符，且必须以字母或数字开头"

        return True, "Agent ID 格式正确"

    @staticmethod
    def validate_agent_name(name: str) -> tuple[bool, str]:
        """Validate agent name for user input.

        Args:
            name: Agent name to validate

        Returns:
            Tuple of (valid, message)
        """
        if not name:
            return False, "Agent 名字不能为空"

        if len(name) < 1:
            return False, "Agent 名字至少需要 1 个字符"

        if len(name) > 50:
            return False, "Agent 名字不能超过 50 个字符"

        return True, "Agent 名字格式正确"


class InputValidator:
    """Validate user input values."""

    @staticmethod
    def validate_temperature(value: str) -> tuple[bool, float, str]:
        """Validate temperature value from user input.

        Args:
            value: Temperature string

        Returns:
            Tuple of (valid, float_value, message)
        """
        try:
            temp = float(value)
            if 0 <= temp <= 2:
                return True, temp, f"Temperature 设置为 {temp}"
            else:
                return False, 0.7, "Temperature 必须在 0-2 之间"
        except ValueError:
            return False, 0.7, "Temperature 必须是数字"

    @staticmethod
    def validate_memory_limit(value: str) -> tuple[bool, str, str]:
        """Validate Docker memory limit from user input.

        Args:
            value: Memory limit string (e.g., "512m", "1g")

        Returns:
            Tuple of (valid, normalized_value, message)
        """
        if not value:
            return True, "512m", "使用默认值 512m"

        pattern = re.compile(r"^(\d+)([mgMG])?$")
        match = pattern.match(value.strip())

        if not match:
            return False, "512m", "格式错误，示例: 512m, 1g"

        num = int(match.group(1))
        unit = (match.group(2) or "m").lower()

        if unit == "g":
            num = num * 1024

        if num < 128:
            return False, "512m", "内存限制至少 128m"

        if num > 16384:
            return False, "512m", "内存限制不能超过 16g"

        normalized = f"{num}m" if num < 1024 else f"{num // 1024}g"
        return True, normalized, f"内存限制设置为 {normalized}"


__all__ = [
    "EnvironmentChecker",
    "APIValidator",
    "AgentValidator",
    "InputValidator",
    "ConfigValidator",  # Re-exported from base.config
]
