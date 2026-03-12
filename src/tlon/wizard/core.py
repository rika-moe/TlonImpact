"""Tlon Wizard - Interactive configuration tool.

Provides interactive terminal interface for configuring Tlon
with Position-Agent architecture.
"""

from pathlib import Path
from typing import Optional

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

try:
    import questionary
    QUESTIONARY_AVAILABLE = True
except ImportError:
    QUESTIONARY_AVAILABLE = False

from ..logger import get_logger
from .menus import AgentMenu, DiagnosticsMenu, PositionMenu, QuickstartWizard, SettingsMenu

logger = get_logger(__name__)


class Wizard:
    """Tlon Configuration Wizard.
    
    Provides interactive menus for:
    - Quick start setup
    - Position management (CRUD)
    - Agent management (CRUD + position transfer)
    - Configuration diagnostics
    - System settings
    """
    
    def __init__(self, 
                 positions_dir: str = "config/positions",
                 agents_dir: str = "config/agents",
                 data_dir: str = "data"):
        """Initialize the wizard.
        
        Args:
            positions_dir: Directory for position configs
            agents_dir: Directory for agent configs
            data_dir: Directory for agent data
        """
        if not RICH_AVAILABLE:
            raise ImportError(
                "Wizard requires 'rich'. "
                "Install with: pip install rich"
            )
        
        if not QUESTIONARY_AVAILABLE:
            raise ImportError(
                "Wizard requires 'questionary'. "
                "Install with: pip install questionary"
            )
        
        self.console = Console()
        self.positions_dir = Path(positions_dir)
        self.agents_dir = Path(agents_dir)
        self.data_dir = Path(data_dir)
        
        # Initialize menu handlers
        self.position_menu = PositionMenu(self.console, positions_dir)
        self.agent_menu = AgentMenu(self.console, agents_dir, positions_dir)
        self.quickstart = QuickstartWizard(self.console, positions_dir, agents_dir, data_dir)
        self.diagnostics_menu = DiagnosticsMenu(self.console, positions_dir, agents_dir)
        self.settings_menu = SettingsMenu(self.console, positions_dir, agents_dir, data_dir)
    
    def run(self) -> None:
        """Run the main wizard loop."""
        self.show_banner()
        
        while True:
            choice = self.show_main_menu()
            
            if choice == "quickstart":
                self.quickstart.run()
            elif choice == "positions":
                self.position_menu.show()
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

    版本: v2.0 (Position-Agent 架构)
    
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
                    "🚀 快速开始 - 初始化完整配置（推荐首次使用）",
                    value="quickstart"
                ),
                questionary.Separator(),
                questionary.Choice(
                    "🏢 岗位管理 - 管理职位模板和协作规则",
                    value="positions"
                ),
                questionary.Choice(
                    "👤 人员管理 - 管理 Agent 和换岗操作",
                    value="agents"
                ),
                questionary.Separator(),
                questionary.Choice(
                    "🔍 配置诊断 - 检查配置完整性和引用关系",
                    value="diagnostics"
                ),
                questionary.Choice(
                    "⚙️  系统设置 - API、存储和导入导出",
                    value="settings"
                ),
                questionary.Separator(),
                questionary.Choice(
                    "🗑️  重置数据 - 清理所有配置和数据（谨慎）",
                    value="reset"
                ),
                questionary.Separator(),
                questionary.Choice("❌ 退出", value="exit"),
            ],
            use_arrow_keys=True,
        ).ask()
        
        return choice or "exit"
    
    def run_reset(self) -> None:
        """Reset all data and configuration."""
        import shutil
        
        self.console.print("\n[bold red]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold red]")
        self.console.print("[bold red]⚠️  警告[/bold red]")
        self.console.print("[bold red]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold red]\n")
        
        self.console.print("此操作将删除以下内容:")
        self.console.print("  ✗ .env 文件")
        self.console.print(f"  ✗ {self.positions_dir}/ 目录 (所有岗位配置)")
        self.console.print(f"  ✗ {self.agents_dir}/ 目录 (所有 Agent 配置)")
        self.console.print(f"  ✗ {self.data_dir}/ 目录 (所有聊天记录和 Agent 记忆)")
        self.console.print("\n保留的内容:")
        self.console.print("  - skills/ 目录 (自定义技能)")
        self.console.print("  - 源代码\n")
        
        confirm = questionary.text(
            '确认重置? [输入 "reset" 确认]:',
        ).ask()
        
        if confirm != "reset":
            self.console.print("[yellow]已取消[/yellow]")
            questionary.press_any_key_to_continue("按回车继续...").ask()
            return
        
        # Delete files
        env_path = Path(".env")
        if env_path.exists():
            env_path.unlink()
            self.console.print("[green]✓ 删除 .env[/green]")
        
        if self.positions_dir.exists():
            shutil.rmtree(self.positions_dir)
            self.console.print(f"[green]✓ 删除 {self.positions_dir}/[/green]")
        
        if self.agents_dir.exists():
            shutil.rmtree(self.agents_dir)
            self.console.print(f"[green]✓ 删除 {self.agents_dir}/[/green]")
        
        if self.data_dir.exists():
            shutil.rmtree(self.data_dir)
            self.console.print(f"[green]✓ 删除 {self.data_dir}/[/green]")
        
        self.console.print("\n[bold]系统已重置为初始状态。[/bold]")
        self.console.print("运行 [dim]tlon wizard[/dim] 重新配置。")
        
        questionary.press_any_key_to_continue("\n按回车返回主菜单...").ask()


def run_wizard() -> int:
    """Run the configuration wizard.
    
    Returns:
        Exit code (0 for success)
    """
    try:
        wizard = Wizard()
        wizard.run()
        return 0
    
    except ImportError as e:
        print("\n" + "="*50)
        print("  Wizard 依赖未安装")
        print("="*50)
        print("\n请安装以下依赖:")
        print("  pip install rich questionary")
        print("\n或者安装完整依赖:")
        print("  pip install -e '.[dev]'")
        print("="*50 + "\n")
        return 1
    
    except Exception as e:
        logger.error(f"Wizard error: {e}", exc_info=True)
        print(f"\n错误: {e}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(run_wizard())
