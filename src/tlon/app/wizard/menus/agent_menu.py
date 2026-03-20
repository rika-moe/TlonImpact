"""Agent (personnel) management menu.

Interactive menu for managing Agent configurations
with CRUD operations and position transfer support.
"""

from typing import TYPE_CHECKING

from ..agent_store import AgentStore
from ..position_store import PositionStore

if TYPE_CHECKING:
    from rich.console import Console


class AgentMenu:
    """Menu for agent (personnel) management."""

    def __init__(
        self,
        console: "Console",
        agents_dir: str | None = None,
        positions_dir: str | None = None,
    ):
        """Initialize the agent menu.

        Args:
            console: Rich console for output
            agents_dir: Directory for agent configs (caller should pass explicit path)
            positions_dir: Directory for position configs (caller should pass explicit path)
        """
        self.console = console
        self.agent_store = AgentStore(agents_dir) if agents_dir is not None else AgentStore()
        self.position_store = (
            PositionStore(positions_dir) if positions_dir is not None else PositionStore()
        )

        # Import questionary here for optional dependency
        try:
            import questionary

            self.questionary = questionary
        except ImportError:
            self.questionary = None

    def show(self) -> None:
        """Show the agent management menu."""
        if not self.questionary:
            self.console.print(
                "[red]Wizard requires 'questionary'. Install with: pip install questionary[/red]"
            )
            return

        while True:
            self.console.print("\n[bold]👤 人员管理[/bold]\n")

            choice = self.questionary.select(
                "请选择操作:",
                choices=[
                    self.questionary.Choice("📋 列出所有 Agent", value="list"),
                    self.questionary.Choice("🔍 查看 Agent 详情", value="view"),
                    self.questionary.Choice("➕ 招聘新人", value="create"),
                    self.questionary.Choice("✏️  编辑 Agent", value="edit"),
                    self.questionary.Choice("🔄 换岗", value="transfer"),
                    self.questionary.Choice("🗑️  删除 Agent", value="delete"),
                    self.questionary.Separator(),
                    self.questionary.Choice("← 返回", value="back"),
                ],
            ).ask()

            if choice == "list":
                self.list_agents()
            elif choice == "view":
                self.view_agent()
            elif choice == "create":
                self.create_agent()
            elif choice == "edit":
                self.edit_agent()
            elif choice == "transfer":
                self.transfer_position()
            elif choice == "delete":
                self.delete_agent()
            else:
                break

    def list_agents(self) -> None:
        """List all agents in a table."""
        from rich.table import Table

        agents = self.agent_store.load_all_agents()
        positions = self.position_store.load_all_positions()

        if not agents:
            self.console.print("[yellow]暂无 Agent 配置[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        table = Table(title="Agent 列表")
        table.add_column("ID", style="cyan")
        table.add_column("姓名", style="green")
        table.add_column("岗位", style="yellow")
        table.add_column("模型", style="magenta")

        for _agent_id, agent in sorted(agents.items()):
            position = positions.get(agent.position_id)
            position_title = position.title if position else agent.position_id

            model = agent.llm.model_name if agent.llm else "default"

            table.add_row(
                agent.id,
                agent.name,
                position_title,
                model,
            )

        self.console.print(table)
        self.console.print(f"\n[dim]共 {len(agents)} 个 Agent[/dim]")
        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def view_agent(self) -> None:
        """View agent details."""
        agents = self.agent_store.load_all_agents()
        positions = self.position_store.load_all_positions()

        if not agents:
            self.console.print("[yellow]暂无 Agent 可查看[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        # Select agent
        choices = [
            self.questionary.Choice(f"{a.name} ({a.id})", value=a.id)
            for a in sorted(agents.values(), key=lambda x: x.name)
        ]
        choices.append(self.questionary.Choice("← 返回", value="back"))

        agent_id = self.questionary.select("选择 Agent:", choices=choices).ask()

        if agent_id == "back":
            return

        agent = agents.get(agent_id)
        if not agent:
            return

        # Get position info
        position = positions.get(agent.position_id)
        position_title = position.title if position else agent.position_id

        # Display details
        from rich.panel import Panel

        content = f"""[bold]基本信息[/bold]
ID: {agent.id}
姓名: {agent.name}
个性: {agent.personality or "-"}

[bold]岗位信息[/bold]
岗位: {position_title} ({agent.position_id})
部门: {position.department if position else "-"}
启动模式: {"常驻" if position and position.proactive else "延迟"}

[bold]LLM 配置[/bold]
模型: {agent.llm.model_name if agent.llm else "default"}
Temperature: {agent.llm.temperature if agent.llm else "default"}

[bold]Docker 配置[/bold]
镜像: {agent.docker.image if agent.docker else "default"}
内存限制: {agent.docker.memory_limit if agent.docker else "default"}

[bold]技能[/bold]
"""
        if agent.skills:
            content += ", ".join(agent.skills)
        else:
            content += "暂无技能"

        self.console.print(Panel(content, title=f"Agent 详情: {agent.name}"))
        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def create_agent(self) -> None:
        """Create a new agent (hire new personnel)."""
        self.console.print("\n[bold]➕ 招聘新人[/bold]\n")

        # Step 1: Select position
        positions = self.position_store.load_all_positions()

        if not positions:
            self.console.print("[red]错误: 没有可用的岗位，请先创建岗位[/red]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        self.console.print("[bold]步骤 1/5: 选择岗位[/bold]\n")

        pos_choices = [
            self.questionary.Choice(f"{p.title} - {p.jd[:30]}...", value=p.id)
            for p in sorted(positions.values(), key=lambda x: x.title)
        ]
        pos_choices.append(self.questionary.Choice("← 取消", value="cancel"))

        position_id = self.questionary.select("选择岗位:", choices=pos_choices).ask()

        if position_id == "cancel":
            return

        position = positions.get(position_id)

        # Step 2: Personalization
        self.console.print("\n[bold]步骤 2/5: 个性化设置[/bold]\n")

        name = self.questionary.text("姓名:").ask()
        if not name:
            self.console.print("[red]姓名不能为空[/red]")
            return

        # Suggest personality based on position
        suggested_personality = position.personality if position else "专业、可靠"
        personality = self.questionary.text(
            "个性特征:",
            default=suggested_personality,
        ).ask()

        # Step 3: LLM Config
        self.console.print("\n[bold]步骤 3/5: LLM 配置[/bold]\n")

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
            default="kimi-k2.5",
        ).ask()

        temp_str = self.questionary.text(
            "Temperature [0.7]:",
            default="0.7",
        ).ask()

        try:
            temperature = float(temp_str or "0.7")
        except ValueError:
            temperature = 0.7

        # Step 4: Docker Config
        self.console.print("\n[bold]步骤 4/5: Docker 配置[/bold]\n")

        memory = self.questionary.text(
            "内存限制 [512m]:",
            default="512m",
        ).ask()

        # Step 5: Skills
        self.console.print("\n[bold]步骤 5/6: 技能选择[/bold]\n")

        all_skills = [
            ("file-operations", "文件操作"),
            ("git-operations", "Git操作"),
            ("csv-pipeline", "CSV处理"),
            ("plan-i", "任务规划"),
            ("frontend-design", "前端设计"),
        ]

        skill_choices = [
            self.questionary.Choice(label, value=value, checked=True) for value, label in all_skills
        ]

        selected_skills = (
            self.questionary.checkbox(
                "选择技能 (空格选择/取消):",
                choices=skill_choices,
            ).ask()
            or []
        )

        # Step 6: Proactive setting
        self.console.print("\n[bold]步骤 6/6: 启动模式[/bold]\n")

        proactive = self.questionary.confirm(
            "是否设置为常驻启动?\n常驻: 自动启动，响应所有消息\n延迟: 手动唤醒，只响应@消息",
            default=False,
        ).ask()

        # Generate ID
        existing_ids = list(self.agent_store.load_all_agents().keys())
        agent_id = self.agent_store.generate_agent_id(position_id, existing_ids)

        # Preview
        self.console.print("\n[bold]预览配置[/bold]\n")
        self.console.print(f"ID: {agent_id}")
        self.console.print(f"姓名: {name}")
        self.console.print(f"岗位: {position.title if position else position_id}")
        self.console.print(f"模型: {model}")
        self.console.print(f"启动模式: {'常驻' if proactive else '延迟'}")

        if not self.questionary.confirm("确认创建?").ask():
            self.console.print("[yellow]已取消[/yellow]")
            return

        # Create agent
        from tlon.base.config import AgentConfig, DockerRuntimeConfig, LLMConfig

        agent = AgentConfig(
            id=agent_id,
            name=name,
            personality=personality,
            position_id=position_id,
            llm=LLMConfig(model_name=model, temperature=temperature),
            docker=DockerRuntimeConfig(image="ubuntu:22.04", memory_limit=memory or "512m"),
            skills=selected_skills,
            proactive=proactive,
        )

        success, msg = self.agent_store.save_agent(agent)
        if success:
            self.console.print(f"[green]✓ {msg}[/green]")
            self.console.print("[dim]提示: 重启 server 后新 Agent 生效[/dim]")
        else:
            self.console.print(f"[red]✗ {msg}[/red]")

        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def edit_agent(self) -> None:
        """Edit an existing agent."""
        agents = self.agent_store.load_all_agents()
        positions = self.position_store.load_all_positions()

        if not agents:
            self.console.print("[yellow]暂无 Agent 可编辑[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        # Select agent
        choices = [
            self.questionary.Choice(f"{a.name} ({a.id})", value=a.id)
            for a in sorted(agents.values(), key=lambda x: x.name)
        ]
        choices.append(self.questionary.Choice("← 返回", value="back"))

        agent_id = self.questionary.select("选择要编辑的 Agent:", choices=choices).ask()

        if agent_id == "back":
            return

        agent = agents.get(agent_id)
        if not agent:
            return

        # Edit loop
        while True:
            position = positions.get(agent.position_id)
            position_title = position.title if position else agent.position_id

            self.console.print(f"\n[bold]✏️  编辑 Agent: {agent.name}[/bold]\n")

            # Determine current mode display
            mode_display = "常驻" if agent.proactive else "延迟"

            edit_choice = self.questionary.select(
                "选择要修改的项:",
                choices=[
                    self.questionary.Choice(f"🏷️  姓名 - 当前: {agent.name}", value="name"),
                    self.questionary.Choice(f"💼 岗位 - 当前: {position_title}", value="position"),
                    self.questionary.Choice(
                        f"🎭 个性 - 当前: {agent.personality or '-'}", value="personality"
                    ),
                    self.questionary.Choice(
                        f"🤖 模型 - 当前: {agent.llm.model_name if agent.llm else 'default'}",
                        value="model",
                    ),
                    self.questionary.Choice(
                        f"💾 内存 - 当前: {agent.docker.memory_limit if agent.docker else 'default'}",
                        value="memory",
                    ),
                    self.questionary.Choice(
                        f"⭐ 技能 - 当前: {len(agent.skills)}个", value="skills"
                    ),
                    self.questionary.Choice(
                        f"🚀 启动模式 - 当前: {mode_display}", value="proactive"
                    ),
                    self.questionary.Separator(),
                    self.questionary.Choice("✓ 完成", value="done"),
                ],
            ).ask()

            if edit_choice == "done":
                break

            elif edit_choice == "name":
                new_name = self.questionary.text(
                    "新姓名:",
                    default=agent.name,
                ).ask()
                if new_name:
                    agent.name = new_name
                    self.console.print("[green]✓ 姓名已更新[/green]")

            elif edit_choice == "position":
                # Redirect to transfer
                self._do_transfer(agent, positions)
                # Reload positions in case they changed
                positions = self.position_store.load_all_positions()

            elif edit_choice == "personality":
                new_personality = self.questionary.text(
                    "个性特征:",
                    default=agent.personality,
                ).ask()
                agent.personality = new_personality or ""
                self.console.print("[green]✓ 个性已更新[/green]")

            elif edit_choice == "model":
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
                current_model = agent.llm.model_name if agent.llm else "kimi-k2.5"
                new_model = self.questionary.select(
                    "选择新模型:",
                    choices=models,
                    default=current_model,
                ).ask()
                if new_model:
                    if not agent.llm:
                        from tlon.base.config import LLMConfig

                        agent.llm = LLMConfig(model_name=new_model)
                    else:
                        agent.llm.model_name = new_model
                    self.console.print("[green]✓ 模型已更新[/green]")

            elif edit_choice == "memory":
                current_mem = agent.docker.memory_limit if agent.docker else "512m"
                new_mem = self.questionary.text(
                    "内存限制:",
                    default=current_mem,
                ).ask()
                if new_mem:
                    if not agent.docker:
                        from tlon.base.config import DockerRuntimeConfig

                        agent.docker = DockerRuntimeConfig(
                            image="ubuntu:22.04", memory_limit=new_mem
                        )
                    else:
                        agent.docker.memory_limit = new_mem
                    self.console.print("[green]✓ 内存限制已更新[/green]")

            elif edit_choice == "skills":
                agent.skills = self._edit_skills(agent.skills)

            elif edit_choice == "proactive":
                new_proactive = self.questionary.confirm(
                    "是否设置为常驻启动 (proactive)?\n"
                    "常驻: 自动启动，响应所有消息\n"
                    "延迟: 手动唤醒，只响应@消息",
                    default=agent.proactive,
                ).ask()
                agent.proactive = new_proactive
                mode_str = "常驻" if agent.proactive else "延迟"
                self.console.print(f"[green]✓ 启动模式已更新为: {mode_str}[/green]")

        # Save changes
        success, msg = self.agent_store.save_agent(agent)
        if success:
            self.console.print("[green]✓ Agent 已更新[/green]")
        else:
            self.console.print(f"[red]✗ {msg}[/red]")

        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def _edit_skills(self, current_skills: list) -> list:
        """Interactive skill editor.

        Args:
            current_skills: Current list of skills

        Returns:
            Updated list of skills
        """
        all_skills = [
            ("file-operations", "文件操作"),
            ("git-operations", "Git操作"),
            ("csv-pipeline", "CSV处理"),
            ("plan-i", "任务规划"),
            ("frontend-design", "前端设计"),
        ]

        skill_choices = [
            self.questionary.Choice(label, value=value, checked=value in current_skills)
            for value, label in all_skills
        ]

        selected = (
            self.questionary.checkbox(
                "选择技能 (空格选择/取消):",
                choices=skill_choices,
            ).ask()
            or []
        )

        return selected

    def transfer_position(self) -> None:
        """Transfer an agent to a new position."""
        agents = self.agent_store.load_all_agents()
        positions = self.position_store.load_all_positions()

        if not agents:
            self.console.print("[yellow]暂无 Agent 可换岗[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        # Select agent
        choices = [
            self.questionary.Choice(f"{a.name} ({a.id})", value=a.id)
            for a in sorted(agents.values(), key=lambda x: x.name)
        ]
        choices.append(self.questionary.Choice("← 返回", value="back"))

        agent_id = self.questionary.select("选择要换岗的 Agent:", choices=choices).ask()

        if agent_id == "back":
            return

        agent = agents.get(agent_id)
        if not agent:
            return

        self._do_transfer(agent, positions)

    def _do_transfer(self, agent, positions: dict) -> None:
        """Perform the actual position transfer.

        Args:
            agent: Agent to transfer
            positions: Dictionary of available positions
        """
        from rich.table import Table

        current_position = positions.get(agent.position_id)
        current_title = current_position.title if current_position else agent.position_id

        self.console.print(f"\n[bold]🔄 换岗: {agent.name}[/bold]\n")
        self.console.print(f"当前岗位: {current_title} ({agent.position_id})\n")

        # Select new position
        other_positions = [(pid, p) for pid, p in positions.items() if pid != agent.position_id]

        if not other_positions:
            self.console.print("[yellow]没有其他岗位可选[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        pos_choices = [
            self.questionary.Choice(
                f"{p.title} - {p.department or '无部门'} {'[常驻]' if p.proactive else ''}",
                value=pid,
            )
            for pid, p in sorted(other_positions, key=lambda x: x[1].title)
        ]
        pos_choices.append(self.questionary.Choice("← 取消", value="cancel"))

        new_position_id = self.questionary.select("选择新岗位:", choices=pos_choices).ask()

        if new_position_id == "cancel":
            return

        new_position = positions.get(new_position_id)

        # Show comparison
        self.console.print("\n[bold]变更预览[/bold]\n")

        table = Table()
        table.add_column("项目", style="cyan")
        table.add_column("当前", style="yellow")
        table.add_column("变更后", style="green")

        table.add_row(
            "岗位", current_title, new_position.title if new_position else new_position_id
        )
        table.add_row(
            "部门",
            current_position.department if current_position else "-",
            new_position.department if new_position else "-",
        )
        table.add_row(
            "启动模式",
            "常驻" if current_position and current_position.proactive else "延迟",
            "常驻" if new_position and new_position.proactive else "延迟",
        )
        table.add_row(
            "规则数量",
            str(len(current_position.rules)) if current_position else "0",
            str(len(new_position.rules)) if new_position else "0",
        )

        self.console.print(table)

        self.console.print("\n[yellow]⚠️  换岗将改变该 Agent 的职责和协作规则[/yellow]")

        if not self.questionary.confirm("确认换岗?").ask():
            self.console.print("[yellow]已取消[/yellow]")
            return

        # Perform transfer
        success, msg = self.agent_store.transfer_position(agent.id, new_position_id)
        if success:
            self.console.print(f"[green]✓ {msg}[/green]")
        else:
            self.console.print(f"[red]✗ {msg}[/red]")

        self.questionary.press_any_key_to_continue("按回车继续...").ask()

    def delete_agent(self) -> None:
        """Delete an agent."""
        agents = self.agent_store.load_all_agents()

        if not agents:
            self.console.print("[yellow]暂无 Agent 可删除[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        self.console.print("\n[bold red]⚠️  警告: 删除 Agent 将永久删除其所有数据！[/bold red]\n")

        # Select agent
        choices = [
            self.questionary.Choice(f"{a.name} ({a.id})", value=a.id)
            for a in sorted(agents.values(), key=lambda x: x.name)
        ]
        choices.append(self.questionary.Choice("← 取消", value="cancel"))

        agent_id = self.questionary.select("选择要删除的 Agent:", choices=choices).ask()

        if agent_id == "cancel":
            return

        agent = agents.get(agent_id)
        if not agent:
            return

        # Confirm with ID input
        self.console.print(f"\n即将删除 Agent: {agent.name} ({agent.id})")
        confirm = self.questionary.text(
            f'确认删除? [输入 Agent ID "{agent.id}" 确认]:',
        ).ask()

        if confirm != agent.id:
            self.console.print("[yellow]已取消删除[/yellow]")
            self.questionary.press_any_key_to_continue("按回车继续...").ask()
            return

        # Delete
        success, msg = self.agent_store.delete_agent(agent_id)
        if success:
            self.console.print(f"[green]✓ {msg}[/green]")
        else:
            self.console.print(f"[red]✗ {msg}[/red]")

        self.questionary.press_any_key_to_continue("按回车继续...").ask()
