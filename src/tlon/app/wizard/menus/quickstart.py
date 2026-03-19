"""Quickstart wizard for rapid team setup.

Provides streamlined setup for new Tlon installations
with standard or custom team configurations.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from tlon.base.config import AgentConfig, DockerRuntimeConfig, LLMConfig
from tlon.organization.positions.models import Position

from ..agent_store import AgentStore
from ..position_store import PositionStore
from ..validators import APIValidator, ConfigValidator, EnvironmentChecker

if TYPE_CHECKING:
    from rich.console import Console


class QuickstartWizard:
    """Quickstart wizard for initial setup."""

    # Standard team configuration
    STANDARD_TEAM = [
        ("developer", "林知遥", "技术能力强、注重细节、喜欢钻研"),
        ("operator", "江予白", "沉稳冷静、善于排查问题、注重稳定性"),
        ("project_manager", "沈清晏", "善于沟通、有条理、结果导向"),
        ("customer_service", "顾言蹊", "友好耐心、善于沟通、专业高效"),
        ("product_manager", "苏晚晴", "用户导向、逻辑思维、善于协调"),
        ("data_analyst", "谢云舒", "严谨细致、善于发现规律、数据敏感"),
    ]

    def __init__(
        self,
        console: "Console",
        positions_dir: str = "config/positions",
        agents_dir: str = "config/agents",
        data_dir: str = "data",
    ):
        """Initialize the quickstart wizard.

        Args:
            console: Rich console for output
            positions_dir: Directory for position configs
            agents_dir: Directory for agent configs
            data_dir: Directory for agent data
        """
        self.console = console
        self.positions_dir = Path(positions_dir)
        self.agents_dir = Path(agents_dir)
        self.data_dir = Path(data_dir)
        self.position_store = PositionStore(positions_dir)
        self.agent_store = AgentStore(agents_dir, data_dir)

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
            "Temperature [0.7]:",
            default="0.7",
        ).ask()

        valid, temp, _ = ConfigValidator.validate_temperature(temp_str or "0.7")
        if not valid:
            temp = 0.7

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
                    "🏢 使用标准团队（推荐）- 自动创建6个岗位和6个Agent", value="standard"
                ),
                self.questionary.Choice("⚙️  自定义团队 - 选择需要的岗位并配置", value="custom"),
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

        # Show preview
        from rich.table import Table

        table = Table(title="标准团队预览")
        table.add_column("岗位", style="cyan")
        table.add_column("Agent", style="green")
        table.add_column("启动模式", style="blue")

        templates = self.position_store.get_default_templates()

        for pos_type, name, _ in self.STANDARD_TEAM:
            template = templates.get(pos_type, {})
            mode = "常驻" if template.get("proactive") else "延迟"
            table.add_row(template.get("title", pos_type), name, mode)

        self.console.print(table)

        if not self.questionary.confirm("确认使用标准团队配置?").ask():
            return None

        # Build configuration
        positions = []
        agents = []

        for pos_type, name, personality in self.STANDARD_TEAM:
            template = templates.get(pos_type)
            if not template:
                continue

            # Create position
            position = Position(
                type=template["type"],
                title=template["title"],
                jd=template["jd"],
                department=template.get("department", ""),
                proactive=template.get("proactive", False),
                rules=template.get("rules", []),
                prompt_template=template.get("prompt_template"),
            )
            positions.append(position)

            # Create agent
            agent = AgentConfig(
                id=f"{pos_type}-001",
                name=name,
                personality=personality,
                position_id=pos_type,
                llm=LLMConfig(model_name=default_model, temperature=0.7),
                docker=DockerRuntimeConfig(image="ubuntu:22.04", memory_limit="512m"),
                skills=[],
            )
            agents.append(agent)

        return {
            "type": "standard",
            "positions": positions,
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

        # Load available positions from templates
        templates = self.position_store.get_default_templates()

        # Select positions
        pos_choices = [
            self.questionary.Choice(
                f"{t['title']} - {t['jd'][:30]}...", value=pos_type, checked=True
            )
            for pos_type, t in sorted(templates.items(), key=lambda x: x[1]["title"])
        ]

        selected_positions = self.questionary.checkbox(
            "选择需要的岗位 (空格选择/取消):",
            choices=pos_choices,
        ).ask()

        if not selected_positions:
            self.console.print("[yellow]未选择任何岗位[/yellow]")
            return None

        # Create positions and configure agents
        positions = []
        agents = []

        for pos_type in selected_positions:
            template = templates.get(pos_type)
            if not template:
                continue

            # Create position
            position = Position(
                type=template["type"],
                title=template["title"],
                jd=template["jd"],
                department=template.get("department", ""),
                proactive=template.get("proactive", False),
                rules=template.get("rules", []),
                prompt_template=template.get("prompt_template"),
            )
            positions.append(position)

            # Ask for agent config
            self.console.print(f"\n[bold]配置 Agent: {template['title']}[/bold]")

            if not self.questionary.confirm(f"为 {template['title']} 创建 Agent?").ask():
                continue

            name = self.questionary.text(
                "Agent 姓名:",
                default=template["title"][:2],  # Suggest something
            ).ask()

            if not name:
                continue

            agent = AgentConfig(
                id=f"{pos_type}-001",
                name=name,
                personality=template.get("personality", "专业、可靠"),
                position_id=pos_type,
                llm=LLMConfig(model_name=default_model, temperature=0.7),
                docker=DockerRuntimeConfig(image="ubuntu:22.04", memory_limit="512m"),
                skills=[],
            )
            agents.append(agent)

        return {
            "type": "custom",
            "positions": positions,
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
        table.add_row("Position 数量", str(len(team_config["positions"])))
        table.add_row("Agent 数量", str(len(team_config["agents"])))

        self.console.print(table)

        # Show file list
        self.console.print("\n[bold]将创建以下文件:[/bold]")
        for pos in team_config["positions"]:
            self.console.print(f"  config/positions/{pos.type}.yaml")
        for agent in team_config["agents"]:
            self.console.print(f"  config/agents/{agent.id}.yaml")

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

        # Create positions
        for position in team_config["positions"]:
            success, msg = self.position_store.save_position(position)
            if success:
                self.console.print(f"[green]✓ 创建岗位: {position.title}[/green]")
            else:
                self.console.print(f"[red]✗ 创建岗位失败: {msg}[/red]")

        # Create agents
        for agent in team_config["agents"]:
            success, msg = self.agent_store.save_agent(agent)
            if success:
                self.console.print(f"[green]✓ 创建 Agent: {agent.name}[/green]")
            else:
                self.console.print(f"[red]✗ 创建 Agent 失败: {msg}[/red]")

        # Create data directories
        self._create_data_directories(team_config["agents"])

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
DATA_DIR=./data
"""

        env_path = Path(".env")
        env_path.write_text(env_content)
        self.console.print("[green]✓ 创建 .env[/green]")

    def _create_data_directories(self, agents: list[AgentConfig]) -> None:
        """Create data directories for agents.

        Args:
            agents: List of agents
        """
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "chat_history").mkdir(exist_ok=True)
        (self.data_dir / "agents").mkdir(exist_ok=True)

        for agent in agents:
            agent_dir = self.data_dir / "agents" / agent.id
            agent_dir.mkdir(parents=True, exist_ok=True)

        self.console.print("[green]✓ 创建数据目录结构[/green]")
