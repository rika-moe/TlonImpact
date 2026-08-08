"""System settings menu.

Provides API configuration, storage settings, and
import/export functionality.
"""

import contextlib
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..validators import APIValidator, InputValidator

if TYPE_CHECKING:
    from rich.console import Console


class SettingsMenu:
    """Menu for system settings."""

    def __init__(
        self,
        console: "Console",
        tlon_home: str | None = None,
    ):
        """Initialize the settings menu.

        Args:
            console: Rich console for output
            tlon_home: Tlon home directory (~/.tlon/)
        """
        from tlon.base.paths import paths

        self.console = console
        self.tlon_home = Path(tlon_home) if tlon_home else paths.tlon_home()
        self.env_path = Path(".env")

        try:
            import questionary

            self.questionary: Any = questionary
        except ImportError:
            self.questionary = None

    def show(self) -> None:
        """Show the settings menu."""
        if not self.questionary:
            self.console.print(
                "[red]Wizard requires 'questionary'. Install with: pip install questionary[/red]"
            )
            return

        while True:
            self.console.print("\n[bold]⚙️  系统设置[/bold]\n")

            choice = self.questionary.select(
                "请选择操作:",
                choices=[
                    self.questionary.Choice("🔑 API 设置", value="api"),
                    self.questionary.Choice("💾 存储设置", value="storage"),
                    self.questionary.Choice("📥 导入/导出配置", value="import_export"),
                    self.questionary.Separator(),
                    self.questionary.Choice("← 返回", value="back"),
                ],
            ).ask()

            if choice == "api":
                self.api_settings()
            elif choice == "storage":
                self.storage_settings()
            elif choice == "import_export":
                self.import_export()
            else:
                break

    def api_settings(self) -> None:
        """Configure API settings."""
        self.console.print("\n[bold]🔑 API 设置[/bold]\n")

        # Load current config
        current_config = self._load_env_config()

        self.console.print("当前配置:")
        self.console.print(f"  Base URL: {current_config.get('base_url', '未设置')}")
        self.console.print(f"  Model: {current_config.get('model', '未设置')}")
        self.console.print()

        if not self.questionary.confirm("是否修改 API 配置?").ask():
            return

        # Base URL
        base_url = self.questionary.text(
            "API Base URL:",
            default=current_config.get("base_url", "https://api.openai.com/v1"),
        ).ask()

        if not base_url:
            return

        # API Key
        api_key = self.questionary.password(
            "API Key (留空保持当前值):",
        ).ask()

        if api_key:
            valid, msg = APIValidator.validate_api_key(api_key)
            if not valid:
                self.console.print(f"[red]{msg}[/red]")
                return
        else:
            api_key = current_config.get("api_key", "")

        # Model
        models = [
            "kimi-k2.5",
            "kimi-k2",
            "gpt-4o",
            "gpt-4",
            "gpt-4-turbo",
            "gpt-3.5-turbo",
            "claude-3-opus",
            "claude-3-sonnet",
        ]

        model = self.questionary.select(
            "选择模型:",
            choices=models,
            default=current_config.get("model", "kimi-k2.5"),
        ).ask()

        if not model:
            return

        # Temperature
        temp_str = self.questionary.text(
            "Temperature:",
            default=str(current_config.get("temperature", "1.0")),
        ).ask()

        valid, temp, _ = InputValidator.validate_temperature(temp_str or "1.0")
        if not valid:
            temp = 1.0

        # Save
        self._save_env_config(
            {
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
                "temperature": temp,
            }
        )

        self.console.print("[green]✓ API 配置已更新[/green]")
        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def storage_settings(self) -> None:
        """Configure storage settings."""
        self.console.print("\n[bold]💾 存储设置[/bold]\n")
        self.console.print(f"Tlon 数据目录: {self.tlon_home}\n")

        choice = self.questionary.select(
            "请选择操作:",
            choices=[
                self.questionary.Choice("清理聊天历史记录", value="clean"),
                self.questionary.Choice("← 返回", value="back"),
            ],
        ).ask()

        if choice == "clean" and self.questionary.confirm("确定要清理所有聊天历史记录? 此操作不可撤销！").ask():
            from tlon.base.paths import paths

            chat_history_dir = paths.chat_history_dir()
            if chat_history_dir.exists():
                count = len(list(chat_history_dir.glob("*")))
                shutil.rmtree(chat_history_dir)
                chat_history_dir.mkdir(parents=True, exist_ok=True)
                self.console.print(f"[green]✓ 已清理 {count} 条历史记录[/green]")
            else:
                self.console.print("[yellow]暂无历史记录[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def import_export(self) -> None:
        """Import/export configuration."""
        self.console.print("\n[bold]📥 导入/导出配置[/bold]\n")

        choice = self.questionary.select(
            "请选择操作:",
            choices=[
                self.questionary.Choice("📤 导出所有配置", value="export"),
                self.questionary.Choice("📥 导入配置", value="import"),
                self.questionary.Choice("← 返回", value="back"),
            ],
        ).ask()

        if choice == "export":
            self._export_config()
        elif choice == "import":
            self._import_config()

    def _export_config(self) -> None:
        """Export all configuration to zip."""
        from rich.table import Table

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = f"tlon_config_{timestamp}.zip"

        filename = self.questionary.text(
            "导出文件名:",
            default=default_filename,
        ).ask()

        if not filename:
            return

        agent_count = 0
        try:
            with zipfile.ZipFile(filename, "w", zipfile.ZIP_DEFLATED) as zf:
                # Add agent configs from ~/.tlon/{id}/config/agent.yaml
                if self.tlon_home.exists():
                    for agent_dir in sorted(self.tlon_home.iterdir()):
                        if not agent_dir.is_dir() or agent_dir.name == "shared":
                            continue
                        yaml_file = agent_dir / "config" / "agent.yaml"
                        if yaml_file.exists():
                            zf.write(yaml_file, f"agents/{agent_dir.name}.yaml")
                            agent_count += 1

                # Add .env if exists
                if self.env_path.exists():
                    zf.write(self.env_path, ".env")

            table = Table(title="导出内容")
            table.add_column("类型", style="cyan")
            table.add_column("数量", style="green")

            table.add_row("Agents", str(agent_count))

            self.console.print(table)
            self.console.print(f"\n[green]✓ 配置已导出到: {filename}[/green]")

        except Exception as e:
            self.console.print(f"[red]✗ 导出失败: {e}[/red]")

        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def _import_config(self) -> None:
        """Import configuration from zip."""
        self.console.print("\n[yellow]⚠️  警告: 导入配置将覆盖现有配置！[/yellow]\n")

        filename = self.questionary.text(
            "导入文件路径 (zip 文件):",
        ).ask()

        if not filename:
            return

        filepath = Path(filename)
        if not filepath.exists():
            self.console.print(f"[red]文件不存在: {filename}[/red]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        if not self.questionary.confirm("确认导入并覆盖现有配置?").ask():
            return

        try:
            with zipfile.ZipFile(filepath, "r") as zf:
                agents_files = [
                    n for n in zf.namelist() if n.startswith("agents/") and n.endswith(".yaml")
                ]
                has_env = ".env" in zf.namelist()

                self.console.print("\n导入内容预览:")
                self.console.print(f"  Agents: {len(agents_files)}")
                self.console.print(f"  .env: {'是' if has_env else '否'}")

                if not self.questionary.confirm("确认导入?").ask():
                    return

                # Extract agent YAML files into per-agent config dirs
                for name in agents_files:
                    agent_id = Path(name).stem
                    from tlon.base.paths import paths

                    dest = paths.agent_config_dir(agent_id)
                    zf.extract(name, dest.parent)

                if has_env:
                    zf.extract(".env", Path(""))

            self.console.print("[green]✓ 配置导入成功[/green]")
            self.console.print("[dim]请重启 server 使新配置生效[/dim]")

        except Exception as e:
            self.console.print(f"[red]✗ 导入失败: {e}[/red]")

        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def _load_env_config(self) -> dict:
        """Load configuration from .env file.

        Returns:
            Dictionary of configuration
        """
        config = {
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "model": "kimi-k2.5",
            "temperature": 1.0,
        }

        if self.env_path.exists():
            content = self.env_path.read_text()
            for line in content.split("\n"):
                if line.startswith("OPENAI_BASE_URL="):
                    config["base_url"] = line.split("=", 1)[1].strip()
                elif line.startswith("OPENAI_API_KEY="):
                    config["api_key"] = line.split("=", 1)[1].strip()
                elif line.startswith("OPENAI_MODEL_NAME="):
                    config["model"] = line.split("=", 1)[1].strip()
                elif line.startswith("OPENAI_TEMPERATURE="):
                    with contextlib.suppress(ValueError):
                        config["temperature"] = float(line.split("=", 1)[1].strip())

        return config

    def _save_env_config(self, config: dict) -> None:
        """Save configuration to .env file.

        Reads existing .env, updates only the relevant keys,
        and writes back — preserving all other entries.

        Args:
            config: Configuration dictionary
        """
        key_map = {
            "OPENAI_API_KEY": config.get("api_key", ""),
            "OPENAI_BASE_URL": config.get("base_url", "https://api.openai.com/v1"),
            "OPENAI_MODEL_NAME": config.get("model", "kimi-k2.5"),
            "OPENAI_TEMPERATURE": str(config.get("temperature", 1.0)),
        }

        # Read existing lines, or start fresh
        lines = self.env_path.read_text().splitlines() if self.env_path.exists() else []

        updated_keys: set[str] = set()
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                new_lines.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in key_map:
                new_lines.append(f"{key}={key_map[key]}")
                updated_keys.add(key)
            else:
                new_lines.append(line)

        # Append any keys not yet present
        for key, value in key_map.items():
            if key not in updated_keys:
                new_lines.append(f"{key}={value}")

        self.env_path.write_text("\n".join(new_lines) + "\n")
