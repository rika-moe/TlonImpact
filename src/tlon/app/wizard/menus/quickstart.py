"""Quickstart wizard for rapid team setup.

Provides streamlined setup for new Tlon installations
with standard or custom team configurations.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from tlon.base.config import DockerRuntimeConfig, LLMConfig
from tlon.base.default_team import STANDARD_TEAM_DICTS, make_agent_config

from ..agent_store import AgentStore
from ..server_notify import notify_server_agent_created
from ..validators import APIValidator, EnvironmentChecker

if TYPE_CHECKING:
    from rich.console import Console


class QuickstartWizard:
    """Quickstart wizard for initial setup."""

    def __init__(
        self,
        console: "Console",
        tlon_home: str | None = None,
    ):
        """Initialize the quickstart wizard.

        Args:
            console: Rich console for output
            tlon_home: Tlon home directory (~/.tlon/)
        """
        from tlon.base.paths import paths

        self.console = console
        self.tlon_home = Path(tlon_home) if tlon_home else paths.tlon_home()
        self.agent_store = AgentStore()

        try:
            import questionary

            self.questionary = questionary
        except ImportError:
            self.questionary = None

    def run(self) -> None:
        """Run the quickstart wizard."""
        if not self.questionary:
            self.console.print(
                "[red]Wizard requires 'questionary'. Install with: pip install questionary[/red]"
            )
            return

        self.console.print(
            "\n[bold cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]"
        )
        self.console.print("[bold cyan]🚀 快速开始向导[/bold cyan]")
        self.console.print(
            "[bold cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]\n"
        )

        # Step 1: Environment check
        if not self._step1_environment():
            return

        # Step 2: API configuration
        api_config = self._step2_api_config()
        if not api_config:
            return

        # Step 3: Team configuration
        team_config = self._step3_team_config(api_config["model"])
        if not team_config:
            return

        # Step 4: Confirm and create
        if not self._step4_confirm(api_config, team_config):
            return

        # Create everything
        self._create_configuration(api_config, team_config)

    def _step1_environment(self) -> bool:
        """Step 1: Environment check.

        Returns:
            True if check passed
        """
        self.console.print("[bold]步骤 1/4: 环境检查[/bold]\n")

        checker = EnvironmentChecker()
        success, warnings, errors = checker.check_all()

        self.console.print(checker.get_summary())

        if errors:
            self.console.print("\n[red]✗ 环境检查未通过，请修复以上错误后再试[/red]")
            self.questionary.press_any_key_to_continue("按回车返回主菜单...").ask()
            return False

        if warnings and not self.questionary.confirm("有警告项，是否继续?").ask():
            return False

        self.questionary.press_any_key_to_continue("\n按回车继续...").ask()
        return True

    def _step2_api_config(self) -> dict:
        """Step 2: API configuration.

        Returns:
            API config dict or None if cancelled
        """
        self.console.print("\n[bold]步骤 2/4: API 配置[/bold]\n")

        # Base URL
        base_url = self.questionary.text(
            "API Base URL:",
            default="https://api.openai.com/v1",
        ).ask()

        if not base_url:
            return None

        # API Key
        api_key = self.questionary.password("API Key:", instruction="输入后不会显示").ask()

        if not api_key:
            self.console.print("[red]API Key 不能为空[/red]")
            return None

        valid, msg = APIValidator.validate_api_key(api_key)
        if not valid:
            self.console.print(f"[red]{msg}[/red]")
            return None
        elif "可能不正确" in msg:
            self.console.print(f"[yellow]{msg}[/yellow]")

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

        default_model = "kimi-k2.5" if "kimi" in base_url else "gpt-4"
        model = self.questionary.select(
            "选择默认模型:",
            choices=models,
            default=default_model,
        ).ask()

        if not model:
            return None

        # Test connection
        self.console.print("\n[dim]正在验证 API 连接...[/dim]")
        success, msg, elapsed = APIValidator.test_api_connection(base_url, api_key, model)

        if success:
            self.console.print(f"[green]✓ {msg}[/green]\n")
        else:
            self.console.print(f"[red]✗ {msg}[/red]\n")
            if not self.questionary.confirm("连接测试失败，是否仍要继续?").ask():
                return None

        # Temperature
        temp_str = self.questionary.text(
            "Temperature [1.0]:",
            default="1.0",
        ).ask()

        try:
            temp = float(temp_str or "1.0")
            if not 0 <= temp <= 2:
                temp = 1.0
        except ValueError:
            temp = 1.0
        return {
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "temperature": temp,
        }

    def _step3_team_config(self, default_model: str) -> dict:
        """Step 3: Team configuration.

        Args:
            default_model: Default model for agents

        Returns:
            Team config dict or None if cancelled
        """
        self.console.print("\n[bold]步骤 3/4: 团队配置[/bold]\n")

        setup_type = self.questionary.select(
            "选择初始化方式:",
            choices=[
                self.questionary.Choice(
                    "🏢 使用标准团队（推荐）- 自动创建6个 Agent", value="standard"
                ),
                self.questionary.Choice("⚙️  自定义团队 - 选择需要的角色并配置", value="custom"),
            ],
            default="standard",
        ).ask()

        if setup_type == "standard":
            return self._configure_standard_team(default_model)
        else:
            return self._configure_custom_team(default_model)

    def _configure_standard_team(self, default_model: str) -> dict:
        """Configure standard team.

        Args:
            default_model: Default model for agents

        Returns:
            Team config dict
        """
        self.console.print("\n[bold]标准团队配置[/bold]\n")

        from rich.table import Table

        table = Table(title="标准团队预览")
        table.add_column("Agent", style="green")
        table.add_column("启动模式", style="blue")

        for member in STANDARD_TEAM_DICTS:
            mode = "常驻" if member["proactive"] else "延迟"
            table.add_row(member["name"], mode)

        self.console.print(table)

        if not self.questionary.confirm("确认使用标准团队配置?").ask():
            return None

        agents = []
        for member in STANDARD_TEAM_DICTS:
            agent = make_agent_config(member)
            agent.llm = LLMConfig(model_name=default_model, temperature=1.0)
            agent.docker = DockerRuntimeConfig(image="ubuntu:22.04", memory_limit="512m")
            agents.append({"config": agent, "data": member})

        return {
            "type": "standard",
            "agents": agents,
        }

    def _configure_custom_team(self, default_model: str) -> dict:
        """Configure custom team.

        Args:
            default_model: Default model for agents

        Returns:
            Team config dict or None if cancelled
        """
        self.console.print("\n[bold]自定义团队配置[/bold]\n")

        # Select from standard roles
        role_choices = [
            self.questionary.Choice(f"{m['name']}", value=m["id"], checked=True)
            for m in STANDARD_TEAM_DICTS
        ]

        selected_ids = self.questionary.checkbox(
            "选择需要的 Agent (空格选择/取消):",
            choices=role_choices,
        ).ask()

        if not selected_ids:
            self.console.print("[yellow]未选择任何 Agent[/yellow]")
            return None

        agents = []
        selected_members = [m for m in STANDARD_TEAM_DICTS if m["id"] in selected_ids]

        for member in selected_members:
            self.console.print(f"\n[bold]配置 Agent: {member['name']}[/bold]")

            if not self.questionary.confirm(f"为 {member['name']} 创建 Agent?").ask():
                continue

            name = self.questionary.text(
                "Agent 姓名:",
                default=member["name"],
            ).ask()

            if not name:
                continue

            agent = make_agent_config(member)
            agent.name = name
            agent.llm = LLMConfig(model_name=default_model, temperature=1.0)
            agent.docker = DockerRuntimeConfig(image="ubuntu:22.04", memory_limit="512m")
            agents.append({"config": agent, "data": member})

        return {
            "type": "custom",
            "agents": agents,
        }

    def _step4_confirm(self, api_config: dict, team_config: dict) -> bool:
        """Step 4: Confirm and create.

        Args:
            api_config: API configuration
            team_config: Team configuration

        Returns:
            True if confirmed
        """
        self.console.print("\n[bold]步骤 4/4: 确认创建[/bold]\n")

        from rich.table import Table

        table = Table(title="配置摘要")
        table.add_column("项目", style="cyan")
        table.add_column("值", style="green")

        table.add_row("API Base URL", api_config["base_url"])
        table.add_row("Model", api_config["model"])
        table.add_row("Temperature", str(api_config["temperature"]))
        table.add_row("Agent 数量", str(len(team_config["agents"])))

        self.console.print(table)

        # Show file list
        self.console.print("\n[bold]将创建以下文件:[/bold]")
        for entry in team_config["agents"]:
            a = entry["config"]
            self.console.print(f"  ~/.tlon/{a.id}/config/agent.yaml")
            self.console.print(f"  ~/.tlon/{a.id}/config/IDENTITY.md, SOUL.md, RULES.md")

        return self.questionary.confirm("确认创建以上配置?").ask()

    def _create_configuration(self, api_config: dict, team_config: dict) -> None:
        """Create all configuration files.

        Args:
            api_config: API configuration
            team_config: Team configuration
        """
        self.console.print()

        # Create .env file
        self._create_env_file(api_config)

        # Create agents
        created_ids: list[str] = []
        for entry in team_config["agents"]:
            agent = entry["config"]
            data = entry.get("data", {})
            success, msg = self.agent_store.save_agent(agent)
            if success:
                # Write the canonical total behavior spec
                self.agent_store.write_behavior_file(agent.id)
                # Write narrative .md files
                config_dir = Path(self.agent_store._paths.agent_config_dir(agent.id))
                for field, filename in [
                    ("identity", "IDENTITY.md"),
                    ("profile", "SOUL.md"),
                    ("behavior", "RULES.md"),
                ]:
                    content = data.get(field, "")
                    if content:
                        (config_dir / filename).write_text(content.strip() + "\n")
                created_ids.append(agent.id)
                self.console.print(f"[green]✓ 创建 Agent: {agent.name}[/green]")
            else:
                self.console.print(f"[red]✗ 创建 Agent 失败: {msg}[/red]")

        # Best-effort hot adoption by a running server; silently skipped
        # when no server is up (restart fallback applies)
        for agent_id in created_ids:
            notify_server_agent_created(agent_id)

        # Create shared data directories
        self._create_data_directories()

        # Success message
        self.console.print("\n[green]✓ 配置创建成功！[/green]\n")
        self.console.print("启动服务:")
        self.console.print("  [dim]$ tlon server[/dim]")
        self.console.print("\n启动 TUI 客户端:")
        self.console.print("  [dim]$ tlon tui[/dim]")

        self.questionary.press_any_key_to_continue("\n按回车返回主菜单...").ask()

    def _create_env_file(self, api_config: dict) -> None:
        """Create .env file.

        Args:
            api_config: API configuration
        """
        env_content = f"""# Tlon Configuration
# Generated by Tlon Wizard

# LLM API Configuration
OPENAI_API_KEY={api_config["api_key"]}
OPENAI_BASE_URL={api_config["base_url"]}
OPENAI_MODEL_NAME={api_config["model"]}
OPENAI_TEMPERATURE={api_config["temperature"]}

# Application Settings
LOG_LEVEL=INFO
"""

        env_path = Path(".env")
        env_path.write_text(env_content)
        self.console.print("[green]✓ 创建 .env[/green]")

    def _create_data_directories(self) -> None:
        """Create shared data directories under ~/.tlon/shared/."""
        from tlon.base.paths import paths

        paths.chat_history_dir()
        paths.traces_dir()
        self.console.print("[green]✓ 创建数据目录结构[/green]")
