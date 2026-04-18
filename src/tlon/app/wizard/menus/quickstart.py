"""Quickstart wizard for rapid team setup.

Provides streamlined setup for new Tlon installations
with standard or custom team configurations.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from tlon.base.config import AgentConfig, DockerRuntimeConfig, LLMConfig

from ..agent_store import AgentStore
from ..validators import APIValidator, EnvironmentChecker

if TYPE_CHECKING:
    from rich.console import Console


class QuickstartWizard:
    """Quickstart wizard for initial setup."""

    # Standard team configuration
    STANDARD_TEAM = [
        {
            "id": "developer-001",
            "name": "林知遥",
            "identity": "你是一名技术能力强、注重细节、喜欢钻研的开发工程师。你的习惯是先通过 bash 命令探查环境，再给出解决方案。",
            "profile": "开发工程师，技术部。负责代码编写、Bug 修复和技术方案实现。有技术问题或 Bug 需要处理时可以找我。",
            "behavior": "只在你被@时响应。被@时的工作流程：1. 分析问题 2. 规划方案 3. 调用工具验证 4. 给出完整回复。完成任务后向分派者汇报。",
            "proactive": False,
        },
        {
            "id": "operator-001",
            "name": "江予白",
            "identity": "你是一名沉稳冷静、善于排查问题、注重稳定性的运维工程师。",
            "profile": "运维工程师，技术部。负责系统部署、环境配置和故障处理。有部署或服务器问题时可以找我。",
            "behavior": "只在你被@时响应。谨慎执行可能影响系统的命令，配置变更前最好确认。完成任务后向分派者汇报。",
            "proactive": False,
        },
        {
            "id": "project-manager-001",
            "name": "沈清晏",
            "identity": "你是一名善于沟通、有条理、结果导向的项目经理。",
            "profile": "项目经理，项目部。负责任务协调、进度把控和资源分配。需要协调资源或分派任务时可以找我。",
            "behavior": "只在你被@时响应。不要直接执行技术操作，而是通过分派任务来协调。使用 completeDialog 的 mentions 参数指定负责人。跟踪任务进度并汇总结果向用户汇报。",
            "proactive": True,
        },
        {
            "id": "customer-service-001",
            "name": "顾言蹊",
            "identity": "你是一名友好耐心、善于沟通、专业高效的客服专员。",
            "profile": "客服专员，客服部。用户的第一接触点。负责友好、耐心地接待用户，对问题进行初步诊断和分类。用户有任何问题咨询时可以找我。",
            "behavior": "收到用户消息时主动响应（常驻模式）。对问题进行初步诊断和分类后，使用 completeDialog 的 mentions 参数将问题转派给合适的同事。转派后不要持续跟进，除非用户催促或被明确要求跟进。",
            "proactive": True,
        },
        {
            "id": "product-manager-001",
            "name": "苏晚晴",
            "identity": "你是一名用户导向、逻辑思维、善于协调的产品经理。",
            "profile": "产品经理，产品部。负责需求分析、功能规划和方案评审。需求不清楚或需要方案评审时可以找我。",
            "behavior": "只在你被@时响应。帮助团队理解需求背景和用户场景。使用 plan-i 技能创建规划文档。完成任务后向分派者汇报。",
            "proactive": False,
        },
        {
            "id": "data-analyst-001",
            "name": "谢云舒",
            "identity": "你是一名严谨细致、善于发现规律、数据敏感的数据分析师。",
            "profile": "数据分析师，数据部。负责数据处理、可视化和统计分析。需要数据分析或可视化时可以找我。",
            "behavior": "只在你被@时响应。收到任务时确认收到即可。完成任务后向分派者汇报。",
            "proactive": False,
        },
    ]

    def __init__(
        self,
        console: "Console",
        agents_dir: str | None = None,
        data_dir: str | None = None,
    ):
        """Initialize the quickstart wizard.

        Args:
            console: Rich console for output
            agents_dir: Directory for agent configs (caller should pass explicit path)
            data_dir: Directory for agent data (caller should pass explicit path)
        """
        from tlon.base.settings import get_default_config_dir, get_default_data_dir

        cfg = get_default_config_dir()
        self.console = console
        self.agents_dir = Path(agents_dir) if agents_dir is not None else cfg / "agents"
        self.data_dir = Path(data_dir) if data_dir is not None else get_default_data_dir()
        self.agent_store = (
            AgentStore(agents_dir, data_dir)
            if agents_dir is not None
            else AgentStore(data_dir=str(self.data_dir))
        )

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

        for member in self.STANDARD_TEAM:
            mode = "常驻" if member["proactive"] else "延迟"
            table.add_row(member["name"], mode)

        self.console.print(table)

        if not self.questionary.confirm("确认使用标准团队配置?").ask():
            return None

        agents = []
        for member in self.STANDARD_TEAM:
            agent = AgentConfig(
                id=member["id"],
                name=member["name"],
                identity=member["identity"],
                profile=member["profile"],
                behavior=member["behavior"],
                llm=LLMConfig(model_name=default_model, temperature=1.0),
                docker=DockerRuntimeConfig(image="ubuntu:22.04", memory_limit="512m"),
                proactive=member["proactive"],
            )
            agents.append(agent)

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
            for m in self.STANDARD_TEAM
        ]

        selected_ids = self.questionary.checkbox(
            "选择需要的 Agent (空格选择/取消):",
            choices=role_choices,
        ).ask()

        if not selected_ids:
            self.console.print("[yellow]未选择任何 Agent[/yellow]")
            return None

        agents = []
        selected_members = [m for m in self.STANDARD_TEAM if m["id"] in selected_ids]

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

            agent = AgentConfig(
                id=member["id"],
                name=name,
                identity=member["identity"],
                profile=member["profile"],
                behavior=member["behavior"],
                llm=LLMConfig(model_name=default_model, temperature=1.0),
                docker=DockerRuntimeConfig(image="ubuntu:22.04", memory_limit="512m"),
                proactive=member["proactive"],
            )
            agents.append(agent)

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
