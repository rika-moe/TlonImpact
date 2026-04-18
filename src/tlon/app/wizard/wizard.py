"""Tlon Wizard - Interactive configuration tool.

Provides interactive terminal interface for configuring Tlon.
"""

from pathlib import Path

import questionary
from rich.console import Console
from rich.panel import Panel

from tlon.base.logger import get_logger
from tlon.base.settings import get_default_config_dir, get_default_data_dir

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

    def __init__(
        self,
        agents_dir: str | None = None,
        data_dir: str | None = None,
    ):
        """Initialize the wizard.

        Args:
            agents_dir: Directory for agent configs (default: $TLON_HOME/config/agents)
            data_dir: Directory for agent data (default: $TLON_HOME/data)
        """
        cfg = get_default_config_dir()
        self.agents_dir = Path(agents_dir) if agents_dir is not None else cfg / "agents"
        self.data_dir = Path(data_dir) if data_dir is not None else get_default_data_dir()

        self.console = Console()

        agents_dir_str = str(self.agents_dir)
        data_dir_str = str(self.data_dir)

        self.agent_menu = AgentMenu(self.console, agents_dir_str)
        self.quickstart = QuickstartWizard(self.console, agents_dir_str, data_dir_str)
        self.diagnostics_menu = DiagnosticsMenu(self.console, agents_dir_str)
        self.settings_menu = SettingsMenu(self.console, agents_dir_str, data_dir_str)

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
        self.console.print(f"  ✗ {self.agents_dir}/ 目录 (所有 Agent 配置)")
        self.console.print(f"  ✗ {self.data_dir}/ 目录 (所有聊天记录和 Agent 记忆)")
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

        if self.agents_dir.exists():
            shutil.rmtree(self.agents_dir)
            self.console.print(f"[green]✓ 删除 {self.agents_dir}/[/green]")

        if self.data_dir.exists():
            shutil.rmtree(self.data_dir)
            self.console.print(f"[green]✓ 删除 {self.data_dir}/[/green]")

        self.console.print("\n[bold]系统已重置为初始状态。[/bold]")
        self.console.print("运行 [dim]tlon wizard[/dim] 重新配置。")

        questionary.press_any_key_to_continue("\n按回车返回主菜单...").ask()
