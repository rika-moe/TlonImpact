"""Tlon Wizard - Interactive configuration tool.

Provides interactive terminal interface for configuring Tlon.
"""

from pathlib import Path

import questionary
from rich.console import Console
from rich.panel import Panel

from tlon.base.logger import get_logger
from tlon.base.paths import paths

from .menus.agent_menu import AgentMenu
from .menus.diagnostics_menu import DiagnosticsMenu
from .menus.quickstart import QuickstartWizard
from .menus.settings_menu import SettingsMenu

logger = get_logger(__name__)


class Wizard:
    """Tlon Configuration Wizard.

    Provides interactive menus for:
    - Quick start setup
    - Agent management (CRUD)
    - Configuration diagnostics
    - System settings
    """

    def __init__(self) -> None:
        """Initialize the wizard."""
        self.tlon_home = paths.tlon_home()
        self.console = Console()

        tlon_home_str = str(self.tlon_home)

        self.agent_menu = AgentMenu(self.console, tlon_home_str)
        self.quickstart = QuickstartWizard(self.console, tlon_home_str)
        self.diagnostics_menu = DiagnosticsMenu(self.console, tlon_home_str)
        self.settings_menu = SettingsMenu(self.console, tlon_home_str)

    def run(self) -> None:
        """Run the main wizard loop."""
        self.show_banner()

        while True:
            choice = self.show_main_menu()

            if choice == "quickstart":
                self.quickstart.run()
            elif choice == "agents":
                self.agent_menu.show()
            elif choice == "diagnostics":
                self.diagnostics_menu.show()
            elif choice == "settings":
                self.settings_menu.show()
            elif choice == "reset":
                self.run_reset()
            else:
                self.console.print("\n[dim]再见！[/dim]")
                break

    def show_banner(self) -> None:
        """Display the wizard banner."""
        banner_text = """
🧙‍♂️  欢迎使用 Tlon 配置向导

    使用方向键 ↑↓ 选择菜单，回车确认
        """

        self.console.print(Panel(banner_text, style="cyan", border_style="cyan"))

    def show_main_menu(self) -> str:
        """Display the main menu and get user choice.

        Returns:
            Selected action identifier
        """
        self.console.print("\n[bold]📋 主菜单[/bold]\n")

        choice = questionary.select(
            "请选择操作:",
            choices=[
                questionary.Choice(
                    "🚀 快速开始 - 初始化完整配置（推荐首次使用）", value="quickstart"
                ),
                questionary.Separator(),
                questionary.Choice("👤 人员管理 - 管理 Agent 配置", value="agents"),
                questionary.Separator(),
                questionary.Choice("🔍 配置诊断 - 检查配置完整性", value="diagnostics"),
                questionary.Choice("⚙️  系统设置 - API、存储和导入导出", value="settings"),
                questionary.Separator(),
                questionary.Choice("🗑️  重置数据 - 清理所有配置和数据（谨慎）", value="reset"),
                questionary.Separator(),
                questionary.Choice("❌ 退出", value="exit"),
            ],
            use_arrow_keys=True,
        ).ask()

        return choice or "exit"

    def run_reset(self) -> None:
        """Reset all data and configuration."""
        import shutil

        self.console.print(
            "\n[bold red]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold red]"
        )
        self.console.print("[bold red]⚠️  警告[/bold red]")
        self.console.print(
            "[bold red]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold red]\n"
        )

        self.console.print("此操作将删除以下内容:")
        self.console.print("  ✗ .env 文件")
        self.console.print(f"  ✗ {self.tlon_home}/ 目录下所有 Agent 目录和配置文件")
        self.console.print("\n保留的内容:")
        self.console.print("  - 源代码\n")

        confirm = questionary.text(
            '确认重置? [输入 "reset" 确认]:',
        ).ask()

        if confirm != "reset":
            self.console.print("[yellow]已取消[/yellow]")
            questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        env_path = Path(".env")
        if env_path.exists():
            env_path.unlink()
            self.console.print("[green]✓ 删除 .env[/green]")

        for entry in sorted(self.tlon_home.iterdir()):
            if entry.is_dir():
                shutil.rmtree(entry)
                self.console.print(f"[green]✓ 删除 {entry}/[/green]")
            elif entry.is_file():
                entry.unlink()

        self.console.print("\n[bold]系统已重置为初始状态。[/bold]")
        self.console.print("运行 [dim]tlon wizard[/dim] 重新配置。")

        questionary.press_any_key_to_continue("\n按回车返回主菜单...").ask()
